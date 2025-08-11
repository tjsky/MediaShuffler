# MediaShuffler
一个基于 Python 开发的 Telegram 多媒体内容定时发送机器人，适用于频道运营等用途。

## 🌟 功能特点

- ⭐️每日定时推送内容到频道
- ⭐️支持多管理员权限设置
- ⭐️拥有定时发送文本消息功能，方便频道主定时发广告
- ⭐️每日自动扫描本地资源库文件夹更新数据库
- ⭐️发过的图会自动改名标记，以免重复发送
- ⭐️自适应 Linux 与 windows 系统路径。

## 快速开始

### 准备工作
1. 找 @BotFather 申请一个机器人。
2. 获取机器人的token
3. 将机器人加入频道，设置为管理员并给予发送消息权限。
4. 准备一堆图片和视频（jpg、png、GIF、webp、mp4）

### 部署运行
```
git clone https://github.com/byprogram/MediaShuffler.git
cd MediaShuffler
pip install -r requirements.txt
# 修改config_ex.yaml内对应配置项，并改名为config.yaml
# 把图片视频文件放到设置的路径下
python MediaShuffler.py
# PS: 正式运营，还是需要类似PM2、supervisor之类的进程管理工具，来实现不间断运行、自动重启、失效重启等功能。
```

### 运维命令
- /start 返回bot运行状态
- /set 手动触发图片发送
- /redb 手动刷新数据库（手动更新资源文件夹后刷新数据库）

## 关于
本产品基于Apache协议开源。
服务器推荐RackNerd或CloudCone的就行。
随意Fork，记得保留关于的内容。
TG发图时图片任意长度大于1280的会被压缩为1280px，所以：图片不需要那么高清。

### ⚠️ 免责声明
本项目仅供学习和研究使用。严禁用于违法传播、商业牟利或违反 Telegram 使用政策的用途。开发者不对用户行为承担任何法律责任。
