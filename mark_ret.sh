#!/usr/bin/env bash

# ==========================================
# ======= 配 置 与 常 量 (CONFIG) ==========
# ==========================================
DATAPACKS_DIR="datapacks"
TARGET_FILE="data/minecraft/tags/function/tick.json"
PREFIX="[RET]"

# 颜色代码
COLOR_RED='\033[91m'
COLOR_YELLOW='\033[93m'
COLOR_GREEN='\033[92m'
COLOR_CYAN='\033[96m'
COLOR_RESET='\033[0m'

# ==========================================
# ======= 主 逻 辑 (MAIN LOGIC) ============
# ==========================================

# 1. 前置检查
if [[ ! -d "$DATAPACKS_DIR" ]]; then
    echo -e "${COLOR_RED}[x] 错误: 找不到数据包目录 '$DATAPACKS_DIR'。请确保你在正确的路径下运行。${COLOR_RESET}"
    exit 1
fi

echo -e "${COLOR_CYAN}[*] 开始扫描 '$DATAPACKS_DIR'，寻找包含 '$TARGET_FILE' 的数据包...${COLOR_RESET}\n"

COUNT_MARKED=0
COUNT_SCANNED=0

# 2. 遍历所有 zip 文件
find "$DATAPACKS_DIR" -type f -name "*.zip" -print0 | while IFS= read -r -d $'\0' zip_file; do
    ((COUNT_SCANNED++))
    
    # 提取目录路径和纯文件名
    dir_name=$(dirname "$zip_file")
    base_name=$(basename "$zip_file")
    
    # 防呆设计：如果文件名已经以 [RET] 开头，则跳过，防止重复运行导致出现 [RET][RET]... 的情况
    if [[ "$base_name" == "$PREFIX"* ]]; then
        continue
    fi
    
    # 3. 严格匹配内部路径
    # unzip -l 会列出文件，如果压缩包内确切存在该路径，则返回 0 (成功)
    if unzip -l "$zip_file" "$TARGET_FILE" > /dev/null 2>&1; then
        new_name="${PREFIX}${base_name}"
        new_path="${dir_name}/${new_name}"
        
        # 重命名文件
        if mv "$zip_file" "$new_path"; then
            echo -e "  ${COLOR_GREEN}[+] 发现 tick 函数: ${base_name} -> 已重命名为 ${new_name}${COLOR_RESET}"
            ((COUNT_MARKED++))
        else
            echo -e "  ${COLOR_RED}[x] 重命名失败: ${base_name}${COLOR_RESET}"
        fi
    fi
done

# 4. 总结输出
echo -e "\n${COLOR_CYAN}[*] 扫描完毕! 共检查了 $COUNT_SCANNED 个文件，标记了 $COUNT_MARKED 个包含 tick.json 的数据包。${COLOR_RESET}"