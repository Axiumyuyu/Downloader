#!/usr/bin/env bash
# 从 Modrinth 按列表文件批量下载 plugin/datapack
# 用法: ./downloadlist.sh <列表文件> <MC版本>
# 示例: ./downloadlist.sh packlist.txt 1.21.11

set -e
API_BASE="https://api.modrinth.com/v2"
UA="Modrinth-DownloadList/1.0"

usage() {
    echo "用法: $0 <列表文件> <MC版本>" >&2
    echo "示例: $0 packlist.txt 1.21.11" >&2
    echo "查看 MC 版本列表: list-mc-versions.sh 或 list-mc-versions.sh 1.21  # 过滤前缀" >&2
    exit 1
}

[[ $# -ge 2 ]] || usage
LISTFILE="$1"
MC_VERSION="$2"
[[ -f "$LISTFILE" ]] || { echo "文件不存在: $LISTFILE" >&2; exit 1; }

command -v curl >/dev/null || { echo "需要 curl" >&2; exit 1; }
command -v jq   >/dev/null || { echo "需要 jq" >&2; exit 1; }

# 版本比较: 若 a <= b 返回 0
version_le() {
    local a="$1" b="$2"
    local low; low=$(echo -e "${a}\n${b}" | sort -V | head -n1)
    [[ "$low" == "$a" ]]
}
# 若 a > b 为真（版本 a 更新）
version_gt() {
    local a="$1" b="$2"
    [[ "$a" != "$b" ]] && [[ "$(echo -e "${a}\n${b}" | sort -V | tail -n1)" == "$a" ]]
}

# 解析列表文件，输出行: section\tsubdir\tname （subdir 可能为空）
parse_list() {
    local section="" subdir=""
    while IFS= read -r line || [[ -n "$line" ]]; do
        line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        [[ -z "$line" || "$line" == \#* ]] && continue
        if [[ "$line" == \[*\] ]]; then
            section="${line:1:${#line}-2}"
            section=$(echo "$section" | tr '[:upper:]' '[:lower:]')
            subdir=""
            continue
        fi
        if [[ "$line" == ---dir:* ]]; then
            subdir="${line#---dir:}"
            subdir=$(echo "$subdir" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
            continue
        fi
        [[ -n "$section" ]] && printf "%s\t%s\t%s\n" "$section" "$subdir" "$line"
    done < "$LISTFILE"
}

section_to_output_and_suffix() {
    local section="$1"
    case "$section" in
        plugin|plugins) echo "plugins"$'\t'".jar" ;;
        datapack|datapacks) echo "datapacks"$'\t'".zip" ;;
        *) echo "downloads"$'\t'".jar" ;;
    esac
}

section_to_project_type() {
    local section="$1"
    case "$section" in
        plugin|plugins) echo "plugin" ;;
        datapack|datapacks) echo "datapack" ;;
        *) echo "mod" ;;
    esac
}

# 搜索项目，返回 project_id 或 slug
search_project() {
    local query="$1" pt="$2"
    local q_enc facets_enc
    q_enc=$(printf %s "$query" | jq -sRr @uri)
    facets_enc=$(printf '%s' "[[\"project_type:$pt\"]]" | jq -sRr @uri)
    curl -sS -A "$UA" "${API_BASE}/search?query=${q_enc}&facets=${facets_enc}&limit=1" \
        | jq -r '.hits[0] | .project_id // .slug // empty'
}

# 获取版本列表；若传入第二个参数则过滤 game_versions
get_versions() {
    local pid="$1" gv="$2"
    local url="${API_BASE}/project/${pid}/version"
    [[ -n "$gv" ]] && url="${url}?game_versions=$(printf '%s' "[\"$gv\"]" | jq -sRr @uri)"
    curl -sS -A "$UA" "$url" || echo "[]"
}

# 从 version 的 files 里选一个符合后缀的（优先 primary）
choose_file() {
    local version_json="$1" suffix="$2"
    echo "$version_json" | jq -r --arg suf "$suffix" '
        .files // []
        | map(select((.filename | ascii_downcase | endswith($suf))))
        | if length == 0 then null
          else (.[] | select(.primary == true)) // .[0]
          | .url + "\t" + .filename
          end
    '
}

# 选出一个支持 <= target 的、支持版本最高的 version（stdin 每行一个 JSON）
best_fallback_version() {
    local target="$2" suffix="$3"
    local best_ver="" best_max=""
    while IFS= read -r ver; do
        [[ -z "$ver" ]] && continue
        local file_line; file_line=$(choose_file "$ver" "$suffix")
        [[ -z "$file_line" ]] && continue
        local max_supported
        # 在 shell 里用 sort -V 做版本比较，取该 version 支持且 <= target 的最高版本
        max_supported=$(echo "$ver" | jq -r '.game_versions[]?' | while read -r gv; do
            version_le "$gv" "$target" && echo "$gv"
        done | sort -V | tail -n1)
        [[ -z "$max_supported" ]] && continue
        if [[ -z "$best_max" ]] || version_gt "$max_supported" "$best_max"; then
            best_max="$max_supported"
            best_ver="$ver"
        fi
    done
    echo "$best_ver"
}

download_url() {
    local url="$1" dest="$2"
    mkdir -p "$(dirname "$dest")"
    curl -sSL -A "$UA" -o "$dest" "$url"
}

mkdir -p plugins datapacks
total=0 ok=0 skip=0 fail=0

while IFS=$'\t' read -r section subdir name; do
    ((total++)) || true
    read -r out_base suffix < <(section_to_output_and_suffix "$section")
    pt=$(section_to_project_type "$section")

    project_id=$(search_project "$name" "$pt")
    if [[ -z "$project_id" ]]; then
        echo "[$total] 未找到: $name (类型=$pt)" >&2
        ((fail++)) || true
        continue
    fi

    versions=$(get_versions "$project_id" "$MC_VERSION")
    chosen=""
    od_tag=""
    file_line=""

    # 先找兼容且带正确后缀的 version
    while IFS= read -r ver; do
        [[ -z "$ver" ]] && continue
        file_line=$(choose_file "$ver" "$suffix")
        if [[ -n "$file_line" ]]; then
            chosen="$ver"
            break
        fi
    done < <(echo "$versions" | jq -c '.[]')

    # 无则 fallback：全部版本中选支持 <= MC 且最高的
    if [[ -z "$chosen" ]]; then
        all_versions=$(get_versions "$project_id" "")
        fallback_ver=$(echo "$all_versions" | jq -c '.[]' | best_fallback_version "" "$MC_VERSION" "$suffix")
        if [[ -n "$fallback_ver" ]]; then
            chosen="$fallback_ver"
            od_tag=$(echo "$chosen" | jq -r '[.game_versions[]?] | map(select(. <= "'"$MC_VERSION'"')) | sort | last // "?"')
            file_line=$(choose_file "$chosen" "$suffix")
        fi
    else
        file_line=$(choose_file "$chosen" "$suffix")
    fi

    if [[ -z "$file_line" ]]; then
        echo "[$total] 无兼容版本或无合适文件: $name (MC $MC_VERSION)" >&2
        ((fail++)) || true
        continue
    fi

    url="${file_line%%$'\t'*}"
    filename="${file_line#*$'\t'}"
    [[ -n "$od_tag" ]] && filename="[OD_${od_tag}]${filename}"

    if [[ -n "$subdir" ]]; then
        dest="${out_base}/${subdir}/${filename}"
    else
        dest="${out_base}/${filename}"
    fi

    if [[ -f "$dest" ]]; then
        echo "[$total] 已存在，跳过: $dest"
        ((skip++)) || true
        continue
    fi

    if download_url "$url" "$dest"; then
        echo "[$total] 已下载: $dest"
        ((ok++)) || true
    else
        echo "[$total] 下载失败: $name" >&2
        ((fail++)) || true
    fi
done < <(parse_list)

echo "--- 完成: 成功 $ok, 跳过 $skip, 失败 $fail"
