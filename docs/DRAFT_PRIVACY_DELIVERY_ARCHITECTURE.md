# AI 草稿、隐私与 WhatsApp 发送架构

## 核心边界

系统把一次回复拆成四个阶段：

1. WhatsApp 入站消息先写入该客户独立的 `session.jsonl`。
2. AI 读取该客户历史并生成待审批草稿；草稿不写入聊天历史。
3. 前端按客户号码分别保存输入框内容和 `draftId`，切换客户不会串稿。
4. 人工点击发送后，后端校验 `phone + draftId`，等待本机 WhatsApp bridge 接受请求，再写入最终消息和草稿阶段提出的保险流程状态。

`bridge_confirmed` 只表示 Baileys 的发送调用已被本机桥接接受，不代表对方设备已送达或已读。没有可确认桥接的兼容环境使用 `queued`，不会再返回含义过强的 `sent`。

## 上下文与状态

- WhatsApp 会话键为 `whatsapp:<normalized-phone>`，历史、长期记忆和保险流程状态均按客户隔离。
- 草稿生成会删除历史尾部与当前输入完全相同的那一项，避免同一条客户消息重复进入提示词。
- 草稿阶段在会话副本上计算下一步保险状态，并将其绑定到 `draftId`；废弃草稿不会推进状态。
- 只有审批并成功进入发送通道的同一份草稿，才会提交其状态。

## Agent 权限

WhatsApp 客户对话只暴露：

- `insurance_advisor`：固定的产品筛选和资料核验动作；
- `web_search` / `web_fetch`：受现有网络工具规则约束的资料查询。

通用文件读写、任意 shell、主动消息、子 Agent 和定时任务不会暴露给客户会话。保险查询使用固定脚本和结构化参数，不再让模型创建共享临时文件或执行任意命令。

## 隐私链

- Agent 将会话键作为 `x-session-affinity` 只发送给 `127.0.0.1/localhost/::1` 隐私网关；网关在请求云端前删除此头。
- 文本在本机进行确定性脱敏；云端回复和工具参数返回后再次经过本机脱敏。
- `textOnlyScope` 开启时，图片因为无法检查像素内容而 fail-closed，禁止直接上传云端。
- 调试快照默认关闭；显式开启后也只保存脱敏版本，不保存原始请求或可反查 PII 的 placeholder map。
- 出站只恢复由本地 WhatsApp 元数据验证过的发件人姓名；存在歧义的通用占位符不会盲目恢复。

## 发送确认

每次人工发送生成唯一 `requestId`：

`API -> Python WhatsAppChannel -> Node bridge -> Baileys -> ack(requestId)`

只有匹配的 ack 状态为 `accepted` 时，真实运行环境才将消息记为 `bridge_confirmed`。失败、断连和超时返回 502，并且不会把消息伪装成已发送记录。
