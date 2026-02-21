#!/usr/bin/env kotlin
@file:Repository("https://repo1.maven.org/maven2/")
@file:DependsOn("com.google.code.gson:gson:2.10.1")
@file:DependsOn("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.8.1")
//@file:DependsOn("io.github.z4kn4fein:semver:3.0.0")
@file:DependsOn("/home/axiumyu/MC开发/dl/libs/semver-jvm-3.0.0.jar")

import com.google.gson.Gson
import com.google.gson.GsonBuilder
import com.google.gson.annotations.SerializedName
import com.google.gson.reflect.TypeToken
import io.github.z4kn4fein.semver.toVersion
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import java.io.File
import java.net.URI
import java.net.URLEncoder
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import kotlin.system.exitProcess

// --- 配置与常量 ---
val USER_AGENT = "Gemini/ModrinthLoader/1.1 (archlinux; kotlin; gson)"
val MODRINTH_API = "https://api.modrinth.com/v2"

// --- 数据模型 (Gson 不需要 @Serializable) ---
enum class Category(val dirName: String, val loaders: List<String>, val requiredExt: String) {
    PLUGIN("plugins", listOf("paper", "spigot", "purpur"), ".jar"),
    DATAPACK("datapacks", listOf("datapack"), ".zip"),
    MOD("mods", listOf("fabric", "quilt"), ".jar");

    companion object {
        fun fromHeader(header: String): Category? = when (header.lowercase()) {
            "[plugin]", "[plugins]" -> PLUGIN
            "[datapack]", "[datapacks]" -> DATAPACK
            "[mod]", "[mods]" -> MOD
            else -> null
        }
    }
}

data class DownloadItem(
    val query: String,
    val category: Category,
    val subDir: String?
)

data class SearchResult(val hits: List<ProjectHit>)
data class ProjectHit(val slug: String, val title: String)

data class ProjectVersion(
    val name: String,
    @SerializedName("version_number") val versionNumber: String,
    @SerializedName("game_versions") val gameVersions: List<String>,
    val loaders: List<String>,
    val files: List<VersionFile>
)

data class VersionFile(
    val url: String,
    val filename: String,
    val primary: Boolean
)

// --- HTTP 客户端与 Gson ---
val client: HttpClient = HttpClient.newBuilder()
    .version(HttpClient.Version.HTTP_2)
    .followRedirects(HttpClient.Redirect.NORMAL)
    .build()

// 配置 Gson，虽然 Modrinth 是 snake_case，但为了稳健我们显式使用了 @SerializedName
val gson: Gson = GsonBuilder().create()

// --- 核心逻辑类 ---
class ModrinthLoader(private val targetMcVersion: String, private val inputFile: File) {

    private val targetSemVer = try {
        targetMcVersion.toVersion(strict = false)
    } catch (e: Exception) {
        println("⚠️ 警告: 无法解析目标版本号 $targetMcVersion 为语义版本，回退逻辑将受限。")
        null
    }

    suspend fun run() = coroutineScope {
        val items = parseInputFile()
        if (items.isEmpty()) {
            println("❌ 文件中未找到有效条目。")
            return@coroutineScope
        }

        println("🚀 开始处理 ${items.size} 个项目，目标 MC 版本: $targetMcVersion")
        println("📂 输出将根据 input 文件分类整理...")

        // 使用 Semaphore 限制并发数为 5
        val semaphore = Semaphore(5)
        
        items.map { item ->
            async(Dispatchers.IO) {
                semaphore.withPermit {
                    try {
                        processItem(item)
                    } catch (e: Exception) {
                        println("❌ 处理 ${item.query} 时发生异常: ${e.message}")
                    }
                }
            }
        }.awaitAll()
        
        println("\n✅ 所有任务处理完成。")
    }

    private fun parseInputFile(): List<DownloadItem> {
        val items = mutableListOf<DownloadItem>()
        var currentCategory: Category? = null
        var currentSubDir: String? = null

        if (!inputFile.exists()) return emptyList()

        inputFile.forEachLine { line ->
            val trimmed = line.trim()
            if (trimmed.isEmpty() || trimmed.startsWith("#")) return@forEachLine

            if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
                currentCategory = Category.fromHeader(trimmed)
                currentSubDir = null
                if (currentCategory == null) println("⚠️ 忽略未知分类: $trimmed")
                return@forEachLine
            }

            if (trimmed.startsWith("---dir:")) {
                currentSubDir = trimmed.substringAfter("---dir:").trim()
                return@forEachLine
            }

            if (currentCategory != null) {
                items.add(DownloadItem(trimmed, currentCategory!!, currentSubDir))
            }
        }
        return items
    }

    private suspend fun processItem(item: DownloadItem) {
        print("🔍 [${item.category.name}] 搜索: ${item.query} ... ")
        
        // 1. 搜索项目
        val projectId = searchProject(item.query)
        if (projectId == null) {
            println("\n❌ 未找到项目: ${item.query}")
            return
        }

        // 2. 获取版本列表
        val versions = getVersions(projectId)
        if (versions.isEmpty()) {
            println("\n❌ 项目 $projectId 未找到任何版本信息。")
            return
        }
        
        // 3. 筛选与匹配
        val bestMatch = findBestVersion(versions, item.category)

        if (bestMatch == null) {
            println("\n❌ ${item.query} ($projectId) 没有找到任何兼容 ${item.category.loaders} 且为 ${item.category.requiredExt} 的版本。")
            return
        }

        // 4. 下载
        val (version, isFallback) = bestMatch
        // 优先下载后缀匹配的文件，如果都有后缀则取 primary，或者取第一个
        val fileToDownload = version.files.firstOrNull { it.filename.endsWith(item.category.requiredExt) } 
            ?: version.files.first()

        // 构建路径
        val baseDir = File(item.category.dirName)
        val finalDir = if (item.subDir != null) File(baseDir, item.subDir) else baseDir
        if (!finalDir.exists()) finalDir.mkdirs()

        // 处理文件名
        var finalFilename = fileToDownload.filename
        if (isFallback) {
            val maxSupported = version.gameVersions.maxOrNull() ?: version.versionNumber
            finalFilename = "[OD_$maxSupported]$finalFilename"
        }

        val targetFile = File(finalDir, finalFilename)
        
        if (targetFile.exists()) {
             println("\n⏭️  已存在: ${targetFile.path}")
             return
        }

        println("\n⬇️  下载: ${version.name} -> ${targetFile.path} ${if(isFallback) "(回退)" else ""}")
        downloadFile(fileToDownload.url, targetFile)
    }

    private fun searchProject(query: String): String? {
        val encoded = URLEncoder.encode(query, StandardCharsets.UTF_8)
        val request = HttpRequest.newBuilder()
            .uri(URI.create("$MODRINTH_API/search?query=$encoded&limit=1"))
            .header("User-Agent", USER_AGENT)
            .GET()
            .build()
        
        val response = client.send(request, HttpResponse.BodyHandlers.ofString())
        if (response.statusCode() != 200) return null
        
        val result = gson.fromJson(response.body(), SearchResult::class.java)
        return result.hits.firstOrNull()?.slug
    }

    private fun getVersions(slug: String): List<ProjectVersion> {
        val request = HttpRequest.newBuilder()
            .uri(URI.create("$MODRINTH_API/project/$slug/version"))
            .header("User-Agent", USER_AGENT)
            .GET()
            .build()
        
        val response = client.send(request, HttpResponse.BodyHandlers.ofString())
        if (response.statusCode() != 200) return emptyList()

        // Gson 处理 List 需要使用 TypeToken
        val listType = object : TypeToken<List<ProjectVersion>>() {}.type
        return try {
            gson.fromJson(response.body(), listType)
        } catch (e: Exception) {
            println("解析版本 JSON 失败: ${e.message}")
            emptyList()
        }
    }

    private fun findBestVersion(versions: List<ProjectVersion>, category: Category): Pair<ProjectVersion, Boolean>? {
        // 步骤 1: 过滤 Loader 和 文件后缀
        val compatibleVersions = versions.filter { version ->
            val loaderMatch = version.loaders.any { it in category.loaders }
            // 确保版本中至少有一个文件的后缀符合要求 (例如 .zip 对于 datapack)
            val fileMatch = version.files.any { it.filename.endsWith(category.requiredExt) }
            loaderMatch && fileMatch
        }

        if (compatibleVersions.isEmpty()) return null

        // 步骤 2: 精确匹配 MC 版本
        val exactMatch = compatibleVersions.firstOrNull { version ->
            targetMcVersion in version.gameVersions
        }
        if (exactMatch != null) return exactMatch to false

        // 步骤 3: Fallback (寻找最近的低版本)
        if (targetSemVer == null) return compatibleVersions.firstOrNull() to true

        val fallbackCandidate = compatibleVersions
            .filter { version ->
                // 只考虑所有支持版本都不高于目标版本的 (避免下载到未来的不稳定版本)
                // 或者是那些最高版本确实比目标版本低的
                val maxVersionStr = version.gameVersions.maxOrNull() ?: return@filter false
                try {
                    val maxVer = maxVersionStr.toVersion(strict = false)
                    maxVer < targetSemVer
                } catch (e: Exception) {
                    false 
                }
            }
            .maxByOrNull { version ->
                // 找出版本号最大的那个
                version.gameVersions.mapNotNull { 
                    try { it.toVersion(strict = false) } catch(e: Exception) { null } 
                }.maxOrNull() ?: io.github.z4kn4fein.semver.Version.min
            }

        return if (fallbackCandidate != null) fallbackCandidate to true else null
    }

    private fun downloadFile(url: String, file: File) {
        val request = HttpRequest.newBuilder().uri(URI.create(url)).GET().build()
        val response = client.send(request, HttpResponse.BodyHandlers.ofInputStream())
        if (response.statusCode() == 200) {
            Files.copy(response.body(), file.toPath(), StandardCopyOption.REPLACE_EXISTING)
        } else {
            println("❌ 下载失败 HTTP ${response.statusCode()}: $url")
        }
    }
}

// --- Main Entry ---
if (args.size < 2) {
    println("用法: ./modrinth_loader.main.kts <packlist.txt> <mc_version>")
    exitProcess(1)
}

val inputFile = File(args[0])
val mcVersion = args[1]

runBlocking {
    ModrinthLoader(mcVersion, inputFile).run()
}