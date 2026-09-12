# 验证与安全

> 2026-09-12 内部 APK 对齐：默认开发入口支持 `PluginEntry` APK 插件，声明式通道仍可用。新增 APK 任务字段 `launchType=apk`、`sha256`、`size`、`entryClass`、`downloadUrl`，状态为 `making/downloading/installing/ready/failed`（其中 installing/ready 由端侧管理）。APK 只在 Debug 下载和加载；当前摘要校验不是签名审核，不代表生产发布能力。详情见 [功能对齐与优化分析](08-procedure-alignment-and-optimizations.md)。

目标：默认拒绝、来源可信、行为受控。签名只证明完整与来源，不能把任意代码变成安全插件。

## 1. 信任与威胁

不可信：用户文本、LLM 输出、API、下载内容、包内数据。  
可信：APK、Schema、公钥环、组件表、Runtime、Loader、本地策略、确定性编译器与 Signer。

| 威胁 | 缓解 |
|------|------|
| 传输篡改 | `transportSha256` + 文件 SHA-256 + COSE |
| 假来源 | 内置公钥环，固定 ES256 |
| Zip Slip / 炸弹 | 预扫描路径、压缩比、文件数与总量 |
| 提示注入要危险动作 | 组件/动作/表达式白名单 |
| 死循环 | 无循环原语 + 步数预算 |
| 算法降级 | protected header 固定 `alg=ES256` |
| 回滚旧漏洞包 | `securityEpoch` + 本地高水位 |
| 方案与包错配 | `approvedProposalDigest` |
| 离线植入 | 不可变已装包 + 本地吊销缓存 |
| 日志泄漏 | 字段级脱敏 |

## 2. 签名

Manifest 经 RFC 8785 JCS 后作为 COSE Sign1 detached payload。私钥只在 Signer/KMS。开发钥与发布钥分离。端侧只信 APK 内置根；吊销列表签名下发并缓存。

## 3. 加载前固定次序（失败即拒绝）

| 层 | 失败码 |
|----|--------|
| 1 传输大小与摘要 | `HASH_MISMATCH` |
| 2 ZIP 预扫描 | `ZIP_UNSAFE` |
| 3 布局，仅声明文件 | `PKG_LAYOUT` |
| 4 COSE 验签与吊销 | `SIG_INVALID` |
| 5 Schema，未知字段拒绝 | `SCHEMA_INVALID` |
| 6 与 Ticket 的 id 绑定 | `BINDING_MISMATCH` |
| 7 Runtime/Feature 兼容 | `INCOMPATIBLE` |
| 8 securityEpoch 高水位 | `ROLLBACK_BLOCKED` |
| 9 文件表逐项摘要 | `HASH_MISMATCH` |
| 10 语义、引用、预算、Capability | `SEMANTIC_INVALID` |
| 11 批准摘要 | `APPROVAL_MISMATCH` |

禁止 dex/so/apk/脚本 Entry。先扫 Central Directory，再流式写出。不信任 ZIP header 声明的大小。

## 4. 加载后

新 Runtime 跑完全部 smoke；失败 `ASSERT_FAILED` / `BUDGET_EXCEEDED`。全部通过才允许激活。是否自动打开 UI 由 `autoLaunch` 决定。

禁止 DexClassLoader、反射执行包、JNI、Shell、WebView JS。MVP `capabilities=[]`。

## 5. 断云信任

已激活、纯本地、吊销允许、epoch 不低于高水位、设置允许离线 → 可启动。离线不创建任务、不确认、不下载。页面显示「离线模式」。

## 6. 结论

```text
加载前任一层失败 → 不安装/不加载
smoke 失败 → 不激活
全部通过 → 原子激活，按策略出现在功能列表
```

是否符合用户原话，靠预览确认 + 测试，不用第二个模型当门禁。
