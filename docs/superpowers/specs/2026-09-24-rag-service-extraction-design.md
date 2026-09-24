# T11-A：RAG 服务与索引职责归位

## 确认的范围

用户于2026-09-24选择“服务和索引一起归位”：服务与现有索引实现一起归入 `src/rag`；旧导入路径保留同一对象的兼容导出；迁移测试注入点但保留全部行映射、来源与原子写入校验。不改变检索行为或生产配置。

基线为已完成T01/T03/T05/T06候选 `eb34e79`。独立分支 `codex/rag-service-extraction-pr`；本地候选仍需按顺序发布其前置批次，不能用累计历史大PR替代独立审查。

## 架构与兼容边界

- `src/rag/service.py` 承载原 `src/web/app.py` 的唯一 `RAGSystem` 类，构造、异步初始化、同步/异步嵌入与检索、状态处理方法原样迁移。
- `src/rag/index.py` 承载原 `src/web/rag_index.py` 的manifest、哈希、不可变快照、原子持久化实现。JSON schema、builder_version、异常类、回滚和清理语义不变。
- `src/rag/retrieval.py` 只改索引领域模块的导入；不修改向量标签→row_mapping→源行的任何算法或验证。
- `src/web/app.py` 显式导入同一个 `RAGSystem` 供组装和旧导入使用，不定义包装子类、不伪造 `__module__`、不通过全局app_instance获取服务。
- `src/web/rag_index.py` 只显式导出同一组公共对象：`CURRENT_SCHEMA_VERSION`、`RAGIndexCompatibilityError`、`RAGIndexManifest`、`file_sha256`、`manifest_path`、`load_manifest`、`validate_manifest`、`immutable_index_snapshot`、`atomic_save_index_pair`。不保留第二份实现。
- 类自然归属新模块；旧导入对象identity不变。已有manifest仅JSON持久化，不新增pickle/数据库格式。模块日志来源名可随位置变化，错误/警告信息和对外状态不变。

备选“仅移服务类”保留了领域检索反向依赖Web索引模块，故不选。复制索引或借机重写检索均不采用。

## 回归证明

先补规范模块可独立导入的RED测试，隔离进程禁止加载全局Web应用或发起网络。旧导入与规范类/公共索引对象逐一identity相同。通过AST对照确认类方法和索引实现没有行为改动（只允许import归位/说明文字）。

既有 `test_rag_index_manifest.py` 必须继续真实写临时CSV/FAISS及manifest；hash/read竞争的注入点从app转到service，原子提交fsync/replace注入点从Web索引转到领域索引。攻击时点、失败断言、回滚/临时文件清理断言均保留。平台健康测试改为规范模块名与旧入口identity，不能删掉canonical检查。

运行RAG/实际工厂注入/聊天预算/平台健康/生命周期聚焦回归，再运行相同17路径的Agent联合回归及离线contract。任何可选依赖skip或既有失败如实保留。不是外部模型或真实科研验收。

## 不处理的事项

不修改embedding算法、极值浮点策略、客户端关闭策略、schema、前端、模型配置、科学工具或数据库；不拆其他API领域路由；不删除旧Agent公开接口。发现这些问题需另批复现与设计，不夹带到纯职责迁移。

## 发布

精确写集与双审后本地提交；待前置PR安全合并，再重建精确PR、核对最新CI7/7和无未解决审查后合并。不改main、不部署、不读取真实密钥或生产资产。
