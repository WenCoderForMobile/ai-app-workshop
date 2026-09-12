# 主 Agent 与编程 Agent 的可行性对齐

默认 APK 运行模式下，能力判断使用同一份运行环境快照：

1. 主 LLM 理解用户需求，给出初判和内部技术疑点。
2. `UNSUPPORTED` 不直接反馈为最终结论。已有方案以及标记 `needsTechnicalReview=true` 的技术疑问交给 Codex 编程 Agent，只读评估。
3. 主 LLM 收到初判、用户需求、编程 Agent 的技术意见、环境证据，再做最终决策。
4. 当前可行的方案进入用户确认；确认前不生成插件。

普通偏好缺失（例如颜色、规则未说明）仍直接澄清，不额外调用技术评估。

## 判断结果

| assessment | 行为 |
| --- | --- |
| supported | 必须提供实现路径；进入待确认，最终计划写入开发文档 |
| needs_clarification | 说明缺失信息，不允许直接确认制作 |
| requires_host_changes | 说明需扩展的手机端能力，不把扩展当成当前已有能力 |
| unsupported | 按最终 LLM 的理由说明当前条件下的限制 |

CLI、模型超时或无效 JSON 均保持“技术评估暂未完成”，不据此断言任务不可能，也不按小游戏关键词自动放行。

## 环境证据

`runtime_environment.py` 读取固定白名单源码：宿主与插件 Gradle 配置、Manifest、PluginEntry、PluginLoader、PluginContainerActivity、Gradle Wrapper 配置。

快照同时说明服务端系统/架构、当前 APK 插件运行方式、音效与本地 UI 能力、宿主语音识别和插件接口的区别、资源读取限制、debug 加载限制、当前不允许自动扩展的部署边界。

server 对唯一在线 ADB 设备读取型号、Android SDK 与 ABI；无设备、多设备或读取失败标记 unknown。权限声明不当成运行时授权，源码不当成已安装版本证明。不读取密钥配置、登录文件或整个环境变量，不向模型传输设备序列号。

移除“支付/付款”关键词直接拦截和“小游戏”关键词直接放行；实际支付接入与本地付款记录由模型结合用途、部署约束评估。

## 留档和生成

每次咨询保存在 `code/agent/out/codex_work/feasibility/<独立编号>/review.json`，含环境快照、初判、编程意见、最终结论。咨询采用 `codex exec --sandbox read-only`，不登录、不写插件、不执行编译。

最终意图保留 `feasibility`、`devPlan` 和 `feasibilityReviewPath`；`implementation.md` 写入最终实现路径，`runtime.md` 引用本次环境与评估记录。用户确认后，生成 Agent 可以沿用评审方案。

完整 Python 回归 94 项通过，覆盖主 LLM 推翻初判、主 LLM 否决编程意见、宿主扩展阻止确认、技术与偏好澄清分流、模型失败、无效布尔值、审计记录、设备信息缺失及实现路径归档。本次未发起真实模型请求，不代表新增功能已经经过手机运行验证。

修改 Python 服务后需重启 server 生效；本次没有改动手机 APK 或扩大插件运行权限。
