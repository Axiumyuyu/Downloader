#!/usr/bin/env bash

# ==========================================
# ======= 配 置 与 常 量 (CONFIG) ==========
# ==========================================
DATAPACKS_DIR="datapacks"
RESOURCEPACKS_DIR="resourcepacks"

# 颜色代码，保持与上一个脚本风格一致
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

# 2. 创建目标目录
mkdir -p "$RESOURCEPACKS_DIR"
echo -e "${COLOR_CYAN}[*] 开始扫描 '$DATAPACKS_DIR' 中的数据包并提取资源...${COLOR_RESET}\n"

# 计数器
COUNT_EXTRACTED=0
COUNT_SKIPPED=0

# 3. 使用 find 和 while read 组合，确保能完美处理带空格的文件名和含 ---dir: 的子目录
find "$DATAPACKS_DIR" -type f -name "*.zip" -print0 | while IFS= read -r -d $'\0' zip_file; do
    
    # 提取纯文件名（不含路径和 .zip 后缀）
    base_name=$(basename "$zip_file" .zip)
    
    # 使用 unzip -l 列出压缩包内容，并检查是否包含 assets 文件夹
    # 丢弃输出，只关心退出状态码
    if unzip -l "$zip_file" "assets/*" > /dev/null 2>&1; then
        echo -e "  ${COLOR_GREEN}[+] 发现资源: ${base_name}${COLOR_RESET}"
        
        # 定义并创建输出目标文件夹 (直接在 resourcepacks 下，不含子分类目录)
        target_dir="$RESOURCEPACKS_DIR/$base_name"
        mkdir -p "$target_dir"
        
        # 提取 assets 文件夹及其内部所有内容，以及 pack.mcmeta
        # -q: 静默模式
        # -o: 覆盖已有文件
        # -d: 指定解压目标路径
        unzip -q -o "$zip_file" "assets/*" "pack.mcmeta" -d "$target_dir" > /dev/null 2>&1
        
        # 检查 pack.mcmeta 是否成功解压，如果没有，给个黄色的警告
        if [[ ! -f "$target_dir/pack.mcmeta" ]]; then
             echo -e "      ${COLOR_YELLOW}[!] 警告: 解压了 assets，但压缩包根目录缺失 pack.mcmeta，这可能导致客户端无法识别。${COLOR_RESET}"
        else
             echo -e "      ${COLOR_CYAN}[√] 提取成功 -> $target_dir${COLOR_RESET}"
        fi
        
        ((COUNT_EXTRACTED++))
    else
        echo -e "  ${COLOR_YELLOW}[-] 跳过纯数据包: ${base_name} (未找到 assets)${COLOR_RESET}"
        ((COUNT_SKIPPED++))
    fi

done

# 4. 总结输出
echo -e "\n${COLOR_CYAN}[*] 扫描完毕! 共提取了 $COUNT_EXTRACTED 个资源包，跳过了 $COUNT_SKIPPED 个纯数据包。${COLOR_RESET}"