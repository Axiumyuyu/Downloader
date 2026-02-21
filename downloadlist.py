#!/usr/bin/env python3
import sys
import os
import json
import urllib.request
import urllib.parse
import urllib.error
import re
import socket
import logging

# ==========================================
# ======= 顶 层 常 量 区 (CONSTANTS) =======
# ==========================================

# --- 基础配置 (CONFIG) ---
TIMEOUT = 15
MAX_RETRIES = 3
CHUNK_SIZE = 8192
USER_AGENT = 'ModrinthBulkDownloader/1.4'

# --- 颜色代码 (ANSI COLORS) ---
COLOR_RED = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_GREEN = "\033[92m"
COLOR_CYAN = "\033[96m"
COLOR_RESET = "\033[0m"

# --- 标识符与扩展名 (TOKENS & EXTENSIONS) ---
TOKEN_DIR_MARKER = "---dir:"
SUFFIX_CACHE = ".cache"
EXT_DATAPACK = ".zip"
EXT_PLUGIN = ".jar"

# --- 命名前缀 (PREFIXES) ---
PREFIX_FALLBACK_VER = "[OD_{}]_"      # 版本降级前缀 (Outdated)
PREFIX_FALLBACK_LDR = "[UC_{}]_"      # 加载器降级前缀 (Unconfirmed Compatibility)

# --- API 端点 (URLS) ---
URL_BASE = "https://api.modrinth.com/v2"
URL_SEARCH = URL_BASE + "/search?query={}&limit=1"
URL_PROJECT = URL_BASE + "/project/{}"
URL_VERSION = URL_BASE + "/project/{}/version"

# --- 日志与提示语模板 (LOG MESSAGES) ---
MSG_USAGE = "用法: ./downloadlist.py <packlist.txt> <mc_version>"
MSG_FILE_MISSING = "错误: 找不到文件 '{file}'"
MSG_CACHE_FOUND = "[*] 发现缓存文件 '{file}'，优先从此文件读取配置..."
MSG_TARGET_VER = "目标 Minecraft 版本: {version}\n" + "-"*40
MSG_RETRIEVE = "\n> 正在检索: {query} (目标环境: {loader})"
MSG_HIT_EXACT = "  [*] 标识符精确命中: {title} ({id})"
MSG_HIT_SEARCH = "  [*] 模糊搜索匹配到: {title} ({slug})"
MSG_ERR_NOT_FOUND = "  [-] 未能在 Modrinth 找到有关该名称的匹配项。"
MSG_ERR_NO_VERSIONS = "  [-] 无法获取该项目的版本列表。"

MSG_OK_PERFECT = "  [+] 完美匹配: 兼容 {version} 且原生支持 {loader}。"
MSG_WARN_LDR_ONLY = "  [!] 加载器降级: 找到 {version} 兼容版，但缺乏 {req_loader} 原生声明，回退至 {actual_loader}。"
MSG_WARN_VER_ONLY = "  [!] 版本降级: 原生支持 {loader}，但缺失完美兼容版，回退至支持最高版本 {version} 的发布。"
MSG_WARN_DOUBLE = "  [!] 双重降级: 缺失 {version} 兼容版及 {req_loader} 原生声明，回退至 {actual_loader} ({fb_version})。"
MSG_ERR_NO_VALID = "  [-] 失败: 没有任何符合加载器条件 ({loader}) 且包含 {ext} 后缀的兼容发布。"

MSG_DOWNLOADING = "  [↓] 正在下载: {filename} ..."
MSG_OK_DOWNLOADED = "  [√] 已保存至 {filepath}"
MSG_ERR_DOWNLOAD_RETRY = "  [!] 下载中断 ({err})，正在重新尝试 ({attempt}/{max_retries})..."
MSG_ERR_DOWNLOAD_FAIL = "  [x] 下载最终失败: {err}"
MSG_ERR_API_FAIL = "  [!] API 请求失败 ({url}): {err}"
MSG_WARN_API_RETRY = "  [!] API 响应迟缓或出错，正在重试 ({attempt}/{max_retries})..."
MSG_CACHE_SAVED = "\n[*] 缓存文件已更新并保存至: {file}"

# ==========================================
# ============= 日 志 配 置 ================
# ==========================================

class ColorFormatter(logging.Formatter):
    def format(self, record):
        msg = super().format(record)
        if not sys.stdout.isatty():
            return msg
            
        if record.levelno == logging.WARNING:
            return f"{COLOR_YELLOW}{msg}{COLOR_RESET}"
        elif record.levelno >= logging.ERROR:
            return f"{COLOR_RED}{msg}{COLOR_RESET}"
        elif record.levelno == logging.INFO:
            if "[√]" in record.msg or "[+]" in record.msg:
                return f"{COLOR_GREEN}{msg}{COLOR_RESET}"
            elif ">" in record.msg or "[*]" in record.msg:
                return f"{COLOR_CYAN}{msg}{COLOR_RESET}"
        return msg

logger = logging.getLogger("ModrinthDL")
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(ColorFormatter("%(message)s"))
logger.addHandler(console_handler)

# ==========================================
# ============= 核 心 逻 辑 ================
# ==========================================

def parse_mc_version(v_str):
    matches = re.findall(r'^(\d+)\.(\d+)(?:\.(\d+))?', v_str)
    if matches:
        return tuple(int(x) if x else 0 for x in matches[0])
    return (0, 0, 0)

def evaluate_loader_compat(req_loader, avail_loaders):
    """
    评估加载器兼容性。返回: (是否兼容, 是否需要抛出降级警告, 实际采用的加载器名称)
    """
    req = req_loader.lower()
    avail = [l.lower() for l in avail_loaders]

    # 1. 严格一致匹配 (包含 Fabric, NeoForge, Forge 等一切明确指定的名称)
    if req in avail:
        return True, False, req

    # 2. 跨加载器无损降级 (Purpur 请求 -> Paper 构建)
    if req == "purpur" and "paper" in avail:
        return True, False, "paper"

    # 3. 跨加载器警告降级 (Paper/Purpur 请求 -> Spigot 构建)
    if req in ["paper", "purpur"] and "spigot" in avail:
        return True, True, "spigot"

    return False, False, ""

def fetch_json(url, quiet=False):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if not quiet: logger.error(MSG_ERR_API_FAIL.format(url=url, err=f"HTTP {e.code}"))
            return None
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                if not quiet: logger.warning(MSG_WARN_API_RETRY.format(attempt=attempt+1, max_retries=MAX_RETRIES))
                continue
            if not quiet: logger.error(MSG_ERR_API_FAIL.format(url=url, err=e))
            return None

def main():
    if len(sys.argv) < 3:
        logger.error(MSG_USAGE)
        sys.exit(1)

    list_file = sys.argv[1]
    target_mc_version = sys.argv[2]
    target_v_tuple = parse_mc_version(target_mc_version)
    cache_file = list_file + SUFFIX_CACHE
    
    if os.path.exists(cache_file):
        logger.info(MSG_CACHE_FOUND.format(file=cache_file))
        target_file = cache_file
    else:
        if not os.path.exists(list_file):
            logger.error(MSG_FILE_MISSING.format(file=list_file))
            sys.exit(1)
        target_file = list_file

    with open(target_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    current_loader = "datapack" # 默认 fallback
    current_dir = ""
    new_cache_lines = []

    logger.info(MSG_TARGET_VER.format(version=target_mc_version))

    for line_raw in lines:
        line = line_raw.strip()
        
        if not line or line.startswith('#'):
            new_cache_lines.append(line_raw)
            continue

        # 解析分类 (形如 [paper], [fabric], [datapack])
        if line.startswith('[') and line.endswith(']'):
            current_loader = line[1:-1].lower()
            current_dir = "" 
            new_cache_lines.append(line_raw)
            continue

        if line.startswith(TOKEN_DIR_MARKER):
            current_dir = line.split(TOKEN_DIR_MARKER, 1)[1].strip()
            new_cache_lines.append(line_raw)
            continue

        project_id = download_project(line, current_loader, current_dir, target_mc_version, target_v_tuple)
        
        if project_id:
            new_cache_lines.append(project_id + "\n")
        else:
            new_cache_lines.append(line_raw)

    with open(cache_file, 'w', encoding='utf-8') as f:
        f.writelines(new_cache_lines)
        
    logger.info(MSG_CACHE_SAVED.format(file=cache_file))

def download_project(query, target_loader, sub_dir, target_mc_version, target_v_tuple):
    logger.info(MSG_RETRIEVE.format(query=query, loader=target_loader))
    
    project_id = None
    versions = None
    
    direct_url = URL_PROJECT.format(urllib.parse.quote(query))
    proj_data = fetch_json(direct_url, quiet=True) 
    
    if proj_data and 'id' in proj_data:
        project_id = proj_data['id']
        logger.info(MSG_HIT_EXACT.format(title=proj_data['title'], id=project_id))
        versions = fetch_json(URL_VERSION.format(project_id))
    else:
        search_url = URL_SEARCH.format(urllib.parse.quote(query))
        search_res = fetch_json(search_url)
        if not search_res or not search_res.get('hits'):
            logger.error(MSG_ERR_NOT_FOUND)
            return None

        project = search_res['hits'][0]
        project_id = project['project_id']
        logger.info(MSG_HIT_SEARCH.format(title=project['title'], slug=project['slug']))
        versions = fetch_json(URL_VERSION.format(project_id))
        
    if not versions:
        logger.error(MSG_ERR_NO_VERSIONS)
        return project_id 

    required_ext = EXT_DATAPACK if target_loader == 'datapack' else EXT_PLUGIN
    
    # 构建二维降级匹配矩阵
    best_exact_ver_exact_ldr = None
    best_exact_ver_warn_ldr = None
    best_fb_ver_exact_ldr = None
    best_fb_ver_warn_ldr = None
    
    highest_fb_v = (-1, -1, -1)
    highest_fb_v_str = ""
    highest_fb_v_warn = (-1, -1, -1)
    highest_fb_v_warn_str = ""

    for v in versions:
        valid_files = [f for f in v['files'] if f['filename'].endswith(required_ext)]
        if not valid_files: continue

        # 检查加载器是否兼容
        avail_loaders = v.get('loaders', [])
        is_compat, has_ldr_warning, actual_ldr = evaluate_loader_compat(target_loader, avail_loaders)
        if not is_compat: continue

        file_info = valid_files[0]
        game_versions = v['game_versions']
        
        # 记录前缀组合
        prefix_ldr = PREFIX_FALLBACK_LDR.format(actual_ldr) if has_ldr_warning else ""
        
        if target_mc_version in game_versions:
            if not has_ldr_warning:
                # 找到完美版本（版本完美，加载器完美/无损），直接终止遍历
                best_exact_ver_exact_ldr = (v, file_info, prefix_ldr, actual_ldr)
                break 
            elif not best_exact_ver_warn_ldr:
                best_exact_ver_warn_ldr = (v, file_info, prefix_ldr, actual_ldr)
        else:
            # 记录历史降级版本
            for gv in game_versions:
                gv_tuple = parse_mc_version(gv)
                if gv_tuple < target_v_tuple:
                    prefix_ver = PREFIX_FALLBACK_VER.format(gv)
                    combined_prefix = prefix_ver + prefix_ldr
                    
                    if not has_ldr_warning:
                        if gv_tuple > highest_fb_v:
                            highest_fb_v = gv_tuple
                            highest_fb_v_str = gv
                            best_fb_ver_exact_ldr = (v, file_info, combined_prefix, actual_ldr)
                    else:
                        if gv_tuple > highest_fb_v_warn:
                            highest_fb_v_warn = gv_tuple
                            highest_fb_v_warn_str = gv
                            best_fb_ver_warn_ldr = (v, file_info, combined_prefix, actual_ldr)

    # 按照优先级提取最终决定
    final_decision = None
    if best_exact_ver_exact_ldr:
        final_decision = best_exact_ver_exact_ldr
        logger.info(MSG_OK_PERFECT.format(version=target_mc_version, loader=final_decision[3]))
    elif best_exact_ver_warn_ldr:
        final_decision = best_exact_ver_warn_ldr
        logger.warning(MSG_WARN_LDR_ONLY.format(version=target_mc_version, req_loader=target_loader, actual_loader=final_decision[3]))
    elif best_fb_ver_exact_ldr:
        final_decision = best_fb_ver_exact_ldr
        logger.warning(MSG_WARN_VER_ONLY.format(loader=final_decision[3], version=highest_fb_v_str))
    elif best_fb_ver_warn_ldr:
        final_decision = best_fb_ver_warn_ldr
        logger.warning(MSG_WARN_DOUBLE.format(version=target_mc_version, req_loader=target_loader, actual_loader=final_decision[3], fb_version=highest_fb_v_warn_str))
    else:
        logger.error(MSG_ERR_NO_VALID.format(loader=target_loader, ext=required_ext))
        return project_id

    # 动态确定基础保存目录（如果是 datapack 则存入 datapacks，否则直接使用加载器名称如 paper / fabric 作为目录）
    base_category_dir = "datapacks" if target_loader == "datapack" else target_loader
    target_path = os.path.join(".", base_category_dir)
    if sub_dir:
        target_path = os.path.join(target_path, sub_dir)
    os.makedirs(target_path, exist_ok=True)

    selected_version, file_info, prefix, actual_ldr = final_decision
    filename = prefix + file_info['filename']
    download_url = file_info['url']
    file_path = os.path.join(target_path, filename)

    logger.info(MSG_DOWNLOADING.format(filename=filename))
    
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(download_url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as response, open(file_path, 'wb') as out_file:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk: break
                    out_file.write(chunk)
            logger.info(MSG_OK_DOWNLOADED.format(filepath=file_path))
            break 
        except (socket.timeout, urllib.error.URLError, Exception) as e:
            if attempt < MAX_RETRIES - 1:
                logger.error(MSG_ERR_DOWNLOAD_RETRY.format(err=e, attempt=attempt+1, max_retries=MAX_RETRIES))
            else:
                logger.error(MSG_ERR_DOWNLOAD_FAIL.format(err=e))
                if os.path.exists(file_path): os.remove(file_path)
                    
    return project_id

if __name__ == '__main__':
    main()