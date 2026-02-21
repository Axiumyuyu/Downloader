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
USER_AGENT = 'ModrinthBulkDownloader/1.3'

# --- 颜色代码 (ANSI COLORS) ---
COLOR_RED = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_GREEN = "\033[92m"
COLOR_CYAN = "\033[96m"
COLOR_RESET = "\033[0m"

# --- 标识符与扩展名 (TOKENS & EXTENSIONS) ---
TOKEN_CAT_PLUGIN = "plugin"
TOKEN_CAT_DATAPACK = "datapack"
TOKEN_DIR_MARKER = "---dir:"
SUFFIX_CACHE = ".cache"
EXT_DATAPACK = ".zip"
EXT_PLUGIN = ".jar"
DIR_PLUGINS = "plugins"
DIR_DATAPACKS = "datapacks"
PREFIX_FALLBACK = "[OD_{}]_"

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
MSG_RETRIEVE = "\n> 正在检索: {query}"
MSG_HIT_EXACT = "  [*] 标识符精确命中: {title} ({id})"
MSG_HIT_SEARCH = "  [*] 模糊搜索匹配到: {title} ({slug})"
MSG_ERR_NOT_FOUND = "  [-] 未能在 Modrinth 找到有关该名称的匹配项。"
MSG_ERR_NO_VERSIONS = "  [-] 无法获取该项目的版本列表。"
MSG_OK_COMPATIBLE = "  [+] 找到兼容 {version} 的版本。"
MSG_WARN_FALLBACK = "  [!] 缺失完美兼容版，回退至支持最高版本 {version} 的发布。"
MSG_ERR_NO_VALID_EXT = "  [-] 该项目没有任何包含 {ext} 后缀且兼容或低于目标版本的发布。"
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
    """自定义日志格式化器：根据级别自动上色，若输出被重定向则自动去除颜色"""
    def format(self, record):
        msg = super().format(record)
        # sys.stdout.isatty() 用于判断是否在终端中运行。如果是重定向到文件，它将返回 False
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
logger.setLevel(logging.INFO) # 可以在这里更改为 DEBUG 以查看更多底层信息
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

def fetch_json(url, quiet=False):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if not quiet:
                logger.error(MSG_ERR_API_FAIL.format(url=url, err=f"HTTP {e.code}"))
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

    current_category = DIR_PLUGINS
    current_dir = ""
    new_cache_lines = []

    logger.info(MSG_TARGET_VER.format(version=target_mc_version))

    for line_raw in lines:
        line = line_raw.strip()
        
        if not line or line.startswith('#'):
            new_cache_lines.append(line_raw)
            continue

        if line.startswith('[') and line.endswith(']'):
            cat = line[1:-1].lower()
            if cat == TOKEN_CAT_PLUGIN:
                current_category = DIR_PLUGINS
            elif cat == TOKEN_CAT_DATAPACK:
                current_category = DIR_DATAPACKS
            else:
                current_category = f"{cat}s"
            current_dir = "" 
            new_cache_lines.append(line_raw)
            continue

        if line.startswith(TOKEN_DIR_MARKER):
            current_dir = line.split(TOKEN_DIR_MARKER, 1)[1].strip()
            new_cache_lines.append(line_raw)
            continue

        project_id = download_project(line, current_category, current_dir, target_mc_version, target_v_tuple)
        
        if project_id:
            new_cache_lines.append(project_id + "\n")
        else:
            new_cache_lines.append(line_raw)

    with open(cache_file, 'w', encoding='utf-8') as f:
        f.writelines(new_cache_lines)
        
    logger.info(MSG_CACHE_SAVED.format(file=cache_file))

def download_project(query, category, sub_dir, target_mc_version, target_v_tuple):
    logger.info(MSG_RETRIEVE.format(query=query))
    
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

    required_ext = EXT_DATAPACK if category == DIR_DATAPACKS else EXT_PLUGIN
    
    exact_match = None
    best_fallback = None
    highest_fallback_v_tuple = (-1, -1, -1)
    highest_fallback_v_str = ""

    for v in versions:
        valid_files = [f for f in v['files'] if f['filename'].endswith(required_ext)]
        if not valid_files:
            continue

        game_versions = v['game_versions']
        
        if target_mc_version in game_versions:
            exact_match = (v, valid_files[0])
            break 
            
        for gv in game_versions:
            gv_tuple = parse_mc_version(gv)
            if gv_tuple < target_v_tuple:
                if gv_tuple > highest_fallback_v_tuple:
                    highest_fallback_v_tuple = gv_tuple
                    highest_fallback_v_str = gv
                    best_fallback = (v, valid_files[0])

    prefix = ""
    if exact_match:
        selected_version, file_info = exact_match
        logger.info(MSG_OK_COMPATIBLE.format(version=target_mc_version))
    elif best_fallback:
        selected_version, file_info = best_fallback
        prefix = PREFIX_FALLBACK.format(highest_fallback_v_str)
        # 这里使用 WARNING 等级，Formatter 会自动将其染成黄色
        logger.warning(MSG_WARN_FALLBACK.format(version=highest_fallback_v_str))
    else:
        logger.error(MSG_ERR_NO_VALID_EXT.format(ext=required_ext))
        return project_id

    target_path = os.path.join(".", category)
    if sub_dir:
        target_path = os.path.join(target_path, sub_dir)
    os.makedirs(target_path, exist_ok=True)

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
                    if not chunk:
                        break
                    out_file.write(chunk)
            logger.info(MSG_OK_DOWNLOADED.format(filepath=file_path))
            break 
            
        except (socket.timeout, urllib.error.URLError, Exception) as e:
            if attempt < MAX_RETRIES - 1:
                # 这里使用 ERROR 等级，Formatter 会自动将其染成红色
                logger.error(MSG_ERR_DOWNLOAD_RETRY.format(err=e, attempt=attempt+1, max_retries=MAX_RETRIES))
            else:
                logger.error(MSG_ERR_DOWNLOAD_FAIL.format(err=e))
                if os.path.exists(file_path):
                    os.remove(file_path)
                    
    return project_id

if __name__ == '__main__':
    main()