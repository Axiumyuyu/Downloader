#!/usr/bin/env kscript
@file:DependsOn("com.google.code.gson:gson:2.10.1")

import com.google.gson.*
import java.net.URI
import java.net.URLEncoder
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
val API_BASE = "https://api.modrinth.com/v2"
val gson = Gson()
val client = HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NORMAL).build()

// 解析列表文件，返回 (分类, 子目录?, 项目名)[]
fun parseListFile(path: Path): List<Triple<String, String?, String>> {
    val lines = Files.readAllLines(path, StandardCharsets.UTF_8)
    val result = mutableListOf<Triple<String, String?, String>>()
    var currentSection: String? = null
    var currentSubdir: String? = null

    for (line in lines) {
        val trimmed = line.trim()
        if (trimmed.isEmpty() || trimmed.startsWith("#")) continue
        when {
            trimmed.startsWith("[") && trimmed.endsWith("]") -> {
                currentSection = trimmed.drop(1).dropLast(1).lowercase()
                currentSubdir = null
            }
            trimmed.startsWith("---dir:") -> {
                currentSubdir = trimmed.removePrefix("---dir:").trim()
            }
            currentSection != null -> {
                result.add(Triple(currentSection, currentSubdir, trimmed))
            }
        }
    }
    return result
}

// 分类 -> 输出目录名 与 需要的文件后缀
fun sectionToOutputAndSuffix(section: String): Pair<String, String> = when (section) {
    "plugin", "plugins" -> "plugins" to ".jar"
    "datapack", "datapacks" -> "datapacks" to ".zip"
    else -> "downloads" to ".jar" // 未知分类默认
}

// MC 版本解析为可比较的列表 [1, 21, 11]
fun parseVersion(v: String): List<Int> = v.trim().split(".").map { it.toIntOrNull() ?: 0 }

fun compareVersions(a: String, b: String): Int {
    val va = parseVersion(a)
    val vb = parseVersion(b)
    for (i in 0 until maxOf(va.size, vb.size)) {
        val na = va.getOrElse(i) { 0 }
        val nb = vb.getOrElse(i) { 0 }
        if (na != nb) return na.compareTo(nb)
    }
    return 0
}

// 在 versions 中选出一个支持 <= target 的、支持版本最高的
fun bestFallbackVersion(versions: List<JsonObject>, target: String): JsonObject? {
    val candidates = versions.mapNotNull { ver ->
        val gv = ver.getAsJsonArray("game_versions")?.takeIf { it.size() > 0 }
            ?: return@mapNotNull null
        val supported = (0 until gv.size()).map { gv.get(it).asString }
        val maxSupported = supported.filter { compareVersions(it, target) <= 0 }.maxOrNull() ?: return@mapNotNull null
        Triple(ver, maxSupported, parseVersion(maxSupported))
    }
    if (candidates.isEmpty()) return null
    val best = candidates.maxWithOrNull { a, b -> compareVersions(a.second, b.second) } ?: return null
    return best.first
}

// 从 version 的 files 里选一个符合后缀的（优先 primary）
fun chooseFile(version: JsonObject, suffix: String): JsonObject? {
    val files = version.getAsJsonArray("files") ?: return null
    val withSuffix = (0 until files.size()).map { files.get(it).asJsonObject }
        .filter { it.get("filename").asString.lowercase().endsWith(suffix) }
    val primary = withSuffix.find { it.get("primary").asBoolean }
    return primary ?: withSuffix.firstOrNull()
}

// 搜索项目（模糊取第一个）
fun searchProject(query: String, projectType: String): String? {
    val facet = """[["project_type:$projectType"]]"""
    val q = URLEncoder.encode(query, StandardCharsets.UTF_8)
    val url = "$API_BASE/search?query=$q&facets=${URLEncoder.encode(facet, StandardCharsets.UTF_8)}&limit=1"
    val req = HttpRequest.newBuilder().uri(URI.create(url)).header("User-Agent", "Modrinth-DownloadList/1.0").GET().build()
    val res = client.send(req, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8))
    if (res.statusCode() != 200) return null
    val json = JsonParser.parseString(res.body()).asJsonObject
    val hits = json.getAsJsonArray("hits") ?: return null
    if (hits.size() == 0) return null
    val first = hits.get(0).asJsonObject
    return first.get("project_id")?.asString ?: first.get("slug")?.asString
}

// 获取项目版本（可带 game_versions 过滤）
fun getVersions(projectId: String, gameVersions: List<String>?): List<JsonObject> {
    val url = if (gameVersions.isNullOrEmpty()) "$API_BASE/project/$projectId/version"
    else "$API_BASE/project/$projectId/version?game_versions=${URLEncoder.encode(gson.toJson(gameVersions), StandardCharsets.UTF_8)}"
    val req = HttpRequest.newBuilder().uri(URI.create(url)).header("User-Agent", "Modrinth-DownloadList/1.0").GET().build()
    val res = client.send(req, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8))
    if (res.statusCode() != 200) return emptyList()
    val arr = JsonParser.parseString(res.body()).getAsJsonArray()
    return (0 until arr.size()).map { arr.get(it).asJsonObject }
}

fun downloadFile(url: String, dest: Path) {
    val req = HttpRequest.newBuilder().uri(URI.create(url)).header("User-Agent", "Modrinth-DownloadList/1.0").GET().build()
    val res = client.send(req, HttpResponse.BodyHandlers.ofByteArray())
    if (res.statusCode() != 200) throw RuntimeException("HTTP ${res.statusCode()}: $url")
    Files.createDirectories(dest.parent)
    Files.write(dest, res.body())
}

fun main(args: Array<String>) {
    if (args.size < 2) {
        System.err.println("用法: downloadlist.main.kts <列表文件> <MC版本>")
        System.err.println("示例: ./downloadlist.main.kts packlist.txt 1.21.11")
        System.err.println("MC 版本列表: 运行 list-mc-versions.main.kts 或 list-mc-versions.sh 查看/补全")
        kotlin.system.exitProcess(1)
    }
    val listPath = Paths.get(args[0])
    val mcVersion = args[1]
    if (!Files.isRegularFile(listPath)) {
        System.err.println("文件不存在: $listPath")
        kotlin.system.exitProcess(1)
    }

    val items = parseListFile(listPath)
    val (outPlugin, outDatapack) = Paths.get("plugins") to Paths.get("datapacks")
    var ok = 0
    var skip = 0
    var fail = 0

    items.forEachIndexed { index, (section, subdir, name) ->
        val (outDirName, suffix) = sectionToOutputAndSuffix(section)
        val outBase = if (outDirName == "plugins") outPlugin else outDatapack
        val projectType = if (outDirName == "plugins") "plugin" else "datapack"

        val projectId = searchProject(name, projectType)
        if (projectId == null) {
            System.err.println("[${index + 1}/${items.size}] 未找到: $name (类型=$projectType)")
            fail++
            return@forEachIndexed
        }

        var versions = getVersions(projectId, listOf(mcVersion))
        var chosen = versions.firstOrNull { chooseFile(it, suffix) != null }
        var odTag: String? = null

        if (chosen == null) {
            val allVersions = getVersions(projectId, null)
            val fallback = bestFallbackVersion(allVersions.filter { chooseFile(it, suffix) != null }, mcVersion)
            if (fallback != null) {
                chosen = fallback
                val gv = fallback.getAsJsonArray("game_versions")
                val supported = (0 until gv.size()).map { gv.get(it).asString }.filter { compareVersions(it, mcVersion) <= 0 }
                odTag = supported.maxWithOrNull(::compareVersions) ?: "?"
            }
        }

        if (chosen == null) {
            System.err.println("[${index + 1}/${items.size}] 无兼容版本: $name (MC $mcVersion, 类型=$projectType)")
            fail++
            return@forEachIndexed
        }

        val fileObj = chooseFile(chosen, suffix)
        if (fileObj == null) {
            System.err.println("[${index + 1}/${items.size}] 无合适文件: $name")
            fail++
            return@forEachIndexed
        }

        val url = fileObj.get("url").asString
        var filename = fileObj.get("filename").asString
        if (odTag != null) filename = "[OD_${odTag}]$filename"

        val subPath = if (subdir != null) outBase.resolve(subdir).resolve(filename) else outBase.resolve(filename)
        if (Files.exists(subPath)) {
            println("[${index + 1}/${items.size}] 已存在，跳过: $subPath")
            skip++
            return@forEachIndexed
        }

        try {
            downloadFile(url, subPath)
            println("[${index + 1}/${items.size}] 已下载: $subPath")
            ok++
        } catch (e: Exception) {
            System.err.println("[${index + 1}/${items.size}] 下载失败: $name - ${e.message}")
            fail++
        }
    }

    println("--- 完成: 成功 $ok, 跳过 $skip, 失败 $fail")
}
