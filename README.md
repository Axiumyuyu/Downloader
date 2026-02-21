# Modrinth 下载与整理工具

## 简介

这是一个用于下载 Modrinth 上指定内容的 Python 脚本，可以批量下载数据包，插件等，并将其中的资源包整理到指定目录，同时按分类和数据包的性能占用（简单判断是否存在`data/minecraft/tags/function/tick.json`，存在则增加名字前缀以便标识）。

## 使用要求

- Python 3.8 或更高版本
- requests 库

## 使用方法

`python download.py [file name] [Minecraft version]`

## 参数说明

- `[file name]`：批量下载列表的文件名
- `[Minecrft version]`：要下载的模组包的 Minecraft 版本，例如`1.21.11`, `1.20.1`等

## 文件说明

文件内容类似这样的列表：

```
[plugin]
viaversion
minimotd
chunky

---dir:admin
world edit
axiom

[datapack]

---dir:分类1
terralith
incendium
effective hoe [purpur pack]

---dir:分类2
don't fight music
```

其中:
  - `[plugin]`和`[datapack]`表示要下载的类型，`---dir:分类`表示要下载的模组包的目录，未读取到时存放在类型的根目录下
  - `viaversion`和`terralith`表示要下载的模组包的名称(模糊匹配)。

## 注意事项
  - 脚本会忽略空行和以`#`开头的注释行
  - 下载的插件默认为paper系列，个别插件可能不兼容spigot


## 缓存机制
  - 下载完成后会将项目唯一标识符写入缓存文件（`.cache`文件，下次下载同一列表时直接使用唯一标识符下载，提高下载速度）
  - 若缓存文件不存在或缓存文件中不存在某一项的标识符，则回退到搜索的模糊匹配

## 其他

~~项目除了readme和需求是我做的，其他都是ai slop,但是确实有用~~

这三个脚本主要是方便我自己的使用习惯，可能并不适合其他人