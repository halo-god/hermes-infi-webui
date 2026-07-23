# Hermes Infi WebUI · 行业调研报告

> 本文档为《AICoding 架构设计》核心产物之一，定位为**行业调研报告（research_report）**。
> 上游输入：主理人转交的用户诉求（5 大方向）与 G1 已通过的资料摘要 `material_digest.md`。
> 下游输出：驱动 `business-architect`（业务架构师）的行业调研判断，最终落入《高层架构设计》的 §3 行业调研章节。
>
> **角色定位声明**：本报告的加权打分与结论均属「建议」而非「裁决」。依据中间确认协议 §2.1 澄清与用户主理人指令，研究打分最优**不构成**对下游业务架构师的「已冻结」输入；下游仍保留对自研/采购/复用边界与 MVP 范围的完整裁决权。

---

## 0. 元信息：修订记录

```yaml
标题: Hermes Infi WebUI - 行业调研报告 v0.1
版本: v0.1
状态: Reviewing   # Draft | Reviewing | Approved | Deprecated
创建日期: 2026-07-21
最后更新: 2026-07-21
调研人: research-analyst（查有据）
审核人:
  - 主理人（team-lead）

关联文档:
  上游输入:
    - 用户诉求: 由主理人注入（5 大方向：差异化定位 / 协作模型 / 系统架构 / 关键能力 / 创新点）
    - 调研目标: 由主理人注入（Phase 2 行业调研，G2 审核）
    - 资料基线: material_digest.md（G1 已通过，D1 README / D2 CLAUDE / D3 AGENTS / D4 hermes-agent 上游）
  下游产出:
    - 高层架构设计 §3 行业调研: 将由 business-architect 整合到此章节
```

| 版本 | 日期 | 作者 | 变更内容 | 评审状态 |
| --- | --- | --- | --- | --- |
| v0.1 | 2026-07-21 | research-analyst（查有据） | 初稿（差异化定位 / 协作模型 / 架构与能力 / 创新点 四方向收敛 + 5 家标杆 + 加权矩阵 + 建议 + 风险） | Reviewing |

---

## 1. 调研问题收敛

> 调研启动前，先围绕用户诉求收拢为明确的调研问题集合，确保调研不偏离当前项目背景（Hermes Infi WebUI：基于 Hermes Agent 的自托管 AI Agent 协作平台，支持多用户、团队协作、ACP 驱动）。

### 1.1 原始调研种子

| 编号 | 待验证论题 | 来源（用户诉求要点） | 调研优先级 | 备注 |
| --- | --- | --- | --- | --- |
| S1 | 「真人团队 + 多 AI Agent」相比「个人 + 单 AI」在产品形态与架构需求上的核心差异是什么？ | 诉求① 差异化定位 | 高 | 重点 A |
| S2 | 真人–AI 角色分工、权限边界、任务流转（HITL / 澄清请求 / 审批）、实时通信（SSE/WS、presence/typing/members_changed）的业界做法与开源实现是什么？ | 诉求② 协作模型 | 高 | 重点 B |
| S3 | 前端 WebUI / 后端 / Agent 调度层（ACP/MCP 等）/ 协同中间层的业界方案，以及多角色会话、任务追踪、Agent 编排、协同编辑、审计日志的实现差异？ | 诉求③④ 系统架构 / 关键能力 | 高 | 重点 C |
| S4 | 哪些能力 / 交互模式可作为区别于市面产品的创新点（如真人+多 Agent 混合实时流、澄清请求 HITL、协同编辑）？ | 诉求⑤ 创新点 | 中 | 重点 A/C 衍生 |
| S5 | 自托管 / 私有化约束下，竞品的合规可控（审计、数据驻留、责任归属）能力如何？ | 诉求①③④ | 中 | Hermes 为自托管产品（D1 §1/§17） |

### 1.2 调研问题收敛

> 范围约定（研究裁量，主理人可驳回）：用户诉求「重点 A」列举了产品级竞品（含个人×AI 与团队/多Agent 两类）与 Agent 框架（LangGraph/CrewAI/AutoGen 等）。本调研将**产品级 WebUI / 平台作为评分标杆（B1–B5）**，将 **Agent 框架（LangGraph/CrewAI/AutoGen）作为协作模型技术使能参考（见 §2.3、§4.3）**，不单独作为产品评分标杆——理由：框架是「构建块」而非「终端产品」，其价值在于协作模式（HITL/编排）而非产品级差异化，且用户「重点 B/C」明确要求分析「业界做法与开源实现」「架构与能力的开源/商用实现」，框架据此归入技术参考更贴合。此范围已写入本 §1.2，供下游裁决。

| 编号 | 调研问题 | 调研对象 | 调研目标 | 预期产出 | 关联种子 |
| --- | --- | --- | --- | --- | --- |
| Q1 | 「个人 × AI」与「团队 / 多 Agent」两类协作 WebUI / 平台在产品形态、协作单元、架构需求上的核心差异是什么？ | OpenWebUI、LibreChat、Cursor（个人×AI）；Dify、Microsoft Copilot Studio（团队/多Agent） | 提炼「真人团队 + 多 AI Agent」相比「个人 + 单 AI」的架构需求差异清单 | 差异化维度对比表（§2.3 + §3） | S1, S4 |
| Q2 | 真人成员与 AI Agent 之间的角色分工、权限矩阵、任务流转（human-in-the-loop / 澄清请求 / 审批）与实时通信（SSE/WebSocket、presence/typing/members_changed）的业界做法与开源实现是什么？ | LangGraph/AutoGen/CrewAI（HITL 模式）、ACP（session/request_permission）、MCP（human-in-the-loop）、Slack Events API（实时事件） | 形成可复用于 Hermes 的协作模型模式清单 | 协作模型模式清单（§2.3 + §4.3） | S2 |
| Q3 | 前端 WebUI / 后端服务 / Agent 调度层（ACP/MCP）/ 团队协同中间层的业界方案，以及多角色会话、任务追踪、Agent 编排、协同编辑、审计日志的开源/商用实现与差异化点是什么？ | OpenWebUI/Dify/LibreChat/Copilot Studio 架构与能力；Yjs（协同编辑）；各产品审计能力 | 建立关键能力横向事实表与架构模式参考 | 能力横向事实表（§2.3）+ 架构模式（§4.3） | S3, S5 |
| Q4 | 在 Q1–Q3 事实基础上，哪些能力 / 交互模式可作为 Hermes 区别于市面产品的创新点？ | Devin MultiDevin、Cursor Background Agents、各产品创新功能、Hermes 现有 evt:conv / hermes:clarify 基础 | 给出创新点候选清单并标注与现有基础的契合度 | 创新点候选清单（§4.2 + §4.3） | S4 |

---

## 2. 事实：标杆系统盘点和方案详述

> **四段式「事实」段**。只陈列调研发现的事实，不做引申建议或边界裁决。置信度标注：已核实（有公开来源）/ 推断（基于技术博客或架构描述）/ 综合归纳。

### 2.1 行业标杆清单

**硬指标**：≥ 3 家；至少包含 1 家头部 SaaS 代表 + 1 家开源/自研代表。本次纳入 5 家（3 家开源/自托管代表 + 3 家头部 SaaS 代表，Dify 兼具两者）。

| 编号 | 标杆系统 | 厂商 / 社区 | 部署形态 | 场景覆盖 | 技术亮点 | 商业模式 | 调研来源 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B1 | OpenWebUI | Open WebUI 社区 | 自托管（Docker/K8s/pip）+ 云存储 | 个人×AI + 多用户企业 | RBAC/SSO/SCIM 2.0/MCP/企业审计/水平扩展 | 开源（BSD-3 修正版）+ 企业版 | docs.openwebui.com/features；openwebui.com |
| B2 | Dify | LangGenius | 自托管（开源）+ Cloud SaaS | 团队 / 多 Agent（LLMOps） | Agent 编排/工作流/RBAC/MCP/审计/可视化 | 开源 + 企业版（EE） | dify.ai；dify.ai/startup；dify.ai/blog |
| B3 | Microsoft Copilot Studio | Microsoft | SaaS（M365 服务边界内） | 企业团队 / 多 Agent | 多 Agent 编排/Entra Agent ID/Purview 审计 | 订阅（SaaS） | microsoft.com Copilot Studio；M365 Build 2025 blog |
| B4 | LibreChat | LibreChat 社区 | 自托管（Docker/Helm） | 个人×AI + 多用户 | Agents/MCP/SSO/Admin Panel/Agent Handoffs | 开源（自托管免费） | librechat.ai；librechat.ai/blog（roadmap, v0.8.1） |
| B5 | Cursor | Anysphere | SaaS（桌面 + 云 Agent） | 个人×AI（IDE） | Agent/Background Agents/MCP/Teams | 订阅（SaaS） | cursor.com/pricing；cursor.com/docs/models |

### 2.2 标杆方案详述

> 以下 B1–B5 逐一展开（5 家均有详述）。置信度分三档：已核实 / 推断 / 综合归纳。

#### 2.2.1 B1 - OpenWebUI

| 维度 | 内容 | 置信度 |
| --- | --- | --- |
| 产品定位 | 自托管 AI 平台 Web 界面，"Run AI on your own terms"，连接任意模型（本地或云端），可完全离线运行 | 已核实 |
| 目标用户 | 个人开发者到全球企业（startups to global enterprises），需自托管 / 数据驻留 / 合规的行业 | 已核实 |
| 核心能力 | 多用户（RBAC / 角色 / 用户组）、SSO/OIDC/LDAP、SCIM 2.0 自动用户组配置、API Keys、MCP（Streamable HTTP）、Pipelines 插件、RAG、Artifacts、代码执行、WebSocket/K8s 水平扩展、企业审计日志 / 数据驻留 / air-gapped | 已核实 |
| 架构特点 | 客户端服务端一体（pip/docker 一键部署），Redis-backed sessions 支持多 worker、多节点水平扩展，OpenTelemetry 可观测 | 推断（基于 features 页架构描述） |
| 部署形态 | 自托管（Docker/K8s/pip/bare metal）+ 云存储（S3/GCS/Azure Blob）无状态实例 | 已核实 |
| 集成方式 | REST API、MCP、OpenAPI servers、Pipelines、Python 工具 | 已核实 |
| 定价模式 | 开源免费（Open WebUI License，即 BSD-3-Clause 修正版，保留品牌标识）；企业版含 SLA/LTS/品牌定制（联系销售） | 已核实 |
| 优势 | 自托管可控性极强、社区庞大（458,309 成员）、模型中立、多用户企业特性完整 | 综合归纳 |
| 局限 | 偏「单 AI 对话」范式，原生多 Agent 编排 / 圆桌协同能力弱于 Dify / Copilot Studio；企业高级特性需企业版 | 已核实 + 推断 |
| 对本项目的参考价值 | 自托管多用户 AI WebUI 的基座范式（RBAC/SSO/SCIM/MCP/审计/水平扩展）高度可借鉴；多 Agent 编排需自行补齐 | 推断 |

#### 2.2.2 B2 - Dify

| 维度 | 内容 | 置信度 |
| --- | --- | --- |
| 产品定位 | "Leading Agentic Workflow Builder"，可视化构建生产级 Agent 工作流、RAG、集成、可观测，模型中立 | 已核实 |
| 目标用户 | 从独立开发者到企业团队（"Built for Ambitious Teams"，Volvo 等客户） | 已核实 |
| 核心能力 | 可视化 Workflow Editor、Agent 节点（Function Calling / ReAct 推理策略）、多 Agent 协作（共享会话状态 Session Context、变量传递、条件分支、并行执行、失败重试）、RBAC（4 角色）、多工作区（EE）、MCP 集成 + 发布为 MCP Server、DSL 导出、审计日志、可观测（usage/latency/cost/error）、企业合规 | 已核实 |
| 架构特点 | 可视化画布解耦「智能」与「流程控制」，后端即服务（BaaS），插件治理、多租户 | 推断（基于官方博客） |
| 部署形态 | 自托管（开源）+ Dify Cloud（SaaS） | 已核实 |
| 集成方式 | 模型市场（OpenAI/Anthropic/Gemini/xAI/DeepSeek 等）、工具市场（200+，含 Slack/Perplexity/ComfyUI）、MCP、API | 已核实 |
| 定价模式 | 开源免费（自托管）；Cloud Sandbox 免费 + Professional（小团队）+ 按消息额度 / 触发事件 / 知识请求等计费 | 已核实 |
| 优势 | 多 Agent 编排 + 团队工作区 + 可视化 + 审计/合规最贴合「团队 + AI」；模型中立；生态成熟（100 万+ 应用） | 综合归纳 |
| 局限 | 自托管完整部署比 OpenWebUI 复杂；Agent 节点不能直接嵌套调用 Agent（需通过工具/工作流桥接）；高级多工作区 / 认证在 EE | 已核实 + 推断 |
| 对本项目的参考价值 | 多 Agent 编排（共享会话状态 / 条件分支 / 并行）、团队 RBAC 工作区、可视化、审计/合规是核心借鉴对象；其 Workflow 编排思想可映射到 Hermes 的 Agent 调度层 | 推断 |

#### 2.2.3 B3 - Microsoft Copilot Studio

| 维度 | 内容 | 置信度 |
| --- | --- | --- |
| 产品定位 | 低代码构建可代表用户执行工作的 Agent，用多 Agent 协调流程处理复杂流程，部署于 M365（Teams/SharePoint） | 已核实 |
| 目标用户 | 企业（90% Fortune 500 使用 Copilot Studio；230,000+ 组织） | 已核实 |
| 核心能力 | 多 Agent 编排（Multi-Agent Orchestration，Agents 交换数据 / 分工 / 基于专长 divide work，human oversight）、Copilot Tuning（用公司数据低代码训练）、计算机使用工具、MCP（GA 2025-05）、Entra Agent ID（自动身份）、Purview 信息保护 / 审计、Agent 365 控制面板（注册 / 访问 / 可视化 / 互操作 / 安全）、Power Platform 治理 | 已核实 |
| 架构特点 | 深度绑定 M365 / Power Platform / Dataverse 生态，Agent 作为「一等业务实体」由 Entra 管理身份 | 推断 |
| 部署形态 | SaaS（云端，M365 服务边界内）；数据在 Microsoft 365 服务边界内 | 已核实 |
| 集成方式 | MCP、连接器、Dataverse、M365 应用、Agent Store（合作伙伴 Jira/Monday/Miro） | 已核实 |
| 定价模式 | 订阅；Microsoft 365 Copilot Business $21/用户/月（2025-11 推出，少于 300 用户）；Copilot Studio 按容量 / 消息计费（公开细节有限） | 已核实 |
| 优势 | 企业级多 Agent 编排 + 人机监督 + 身份（Agent ID）+ 审计 / 治理（Purview/Entra/Agent 365）最完整；Teams 深度集成 | 综合归纳 |
| 局限 | 闭源 SaaS，不可自托管 / 不可私有化（数据在 M365 边界）；强绑定微软生态；与 Hermes 自托管定位不符 | 已核实 + 推断 |
| 对本项目的参考价值 | 多 Agent 编排的「人机监督 + 身份（Agent ID）+ 审计 / 治理」治理范式可借鉴（概念层）；部署形态（闭源 SaaS）不借鉴 | 推断 |

#### 2.2.4 B4 - LibreChat

| 维度 | 内容 | 置信度 |
| --- | --- | --- |
| 产品定位 | "The Open-Source AI Platform"，统一可定制 AI 对话界面，最长运行的活跃开源 AI Chat UI | 已核实 |
| 目标用户 | 个人到大型企业 / 教育（41k GitHub stars，43.7M docker pulls，381 contributors） | 已核实 |
| 核心能力 | Agents（文件处理 / 代码解释 / API actions）、Code Interpreter（多语言沙箱）、Artifacts（React/HTML/Mermaid）、MCP、Memory（跨对话持久上下文）、Web Search、SSO（OAuth/SAML/LDAP/2FA）、Admin Panel（CMS：用户 / 用量 / 设置 / 访问控制）、Agent Handoffs（v0.8.1，转交其他专家 Agent）、Redis 分布式 Leader Election（多实例协调） | 已核实 |
| 架构特点 | 多模型提供商统一接入，Agent Handoffs（Beta）实现 Agent 间转交，Redis 分布式 Leader Election 支持集群 | 推断（基于 changelog） |
| 部署形态 | 自托管（Docker/Helm） | 已核实 |
| 集成方式 | 多模型 API、MCP、librechat.yaml 配置、OAuth | 已核实 |
| 定价模式 | 开源免费（自托管） | 已核实 |
| 优势 | 纯开源、多模型、Agents/MCP/Handoffs、企业 SSO/Admin；可作为自托管基座 | 综合归纳 |
| 局限 | 协作以「管理员配置 / 多用户对话」为主，原生团队工作区 / 多 Agent 圆桌协同弱于 Dify；协同编辑未内置 | 已核实 + 推断 |
| 对本项目的参考价值 | 开源多用户 Agents/MCP/SSO/Admin 模式可参考；Agent Handoffs 思路可借鉴到多 Agent 路由 | 推断 |

#### 2.2.5 B5 - Cursor（差异化对照锚点）

| 维度 | 内容 | 置信度 |
| --- | --- | --- |
| 产品定位 | AI 原生 IDE（VS Code 衍生），面向个人的代理式编程工具，「个人 × AI」范式代表 | 已核实 |
| 目标用户 | 个人开发者到团队 / 企业（Teams $40/用户/月，Enterprise 自定义） | 已核实 |
| 核心能力 | Tab 补全、Composer 多文件编辑、Agent、Background Agents（云端 VM 执行任务 / 推送 PR）、Cloud Agents、MCP/skills/hooks、Bugbot（代理式代码审查） | 已核实 |
| 架构特点 | 桌面 IDE + 云端 Agent 执行（Background Agents 在独立 VM 克隆仓库执行）；个人工作流优先 | 推断 |
| 部署形态 | SaaS（桌面应用 + 云 Agent）；Enterprise 提供 on-prem inference 选项 | 已核实 |
| 集成方式 | MCP、模型 API、GitHub/Slack 等 | 已核实 |
| 定价模式 | 订阅（Hobby 免费 / Pro $20/月 / Pro+ $60/月 / Ultra $200/月 / Teams $40/用户/月 / Enterprise 自定义） | 已核实 |
| 优势 | 个人代理式编程体验最佳、Background Agents 异步并行 | 综合归纳 |
| 局限 | 单用户个人范式，无团队实时协作 / 圆桌 / 多 Agent 协同；协作仅为集中计费 / SSO / 审计（企业版），非「真人 + 多 Agent 协同」 | 已核实 + 推断 |
| 对本项目的参考价值 | 作为「个人 × AI」对照锚点，明确 Hermes 应差异化于的方向（非团队协同）；其 Background Agents 异步执行可类比 Hermes 的 Agent Runner 异步调度 | 推断 |

### 2.3 关键技术能力横向事实

> 不评分、不排序，仅按能力维度横陈各方案事实。置信度已并入 §2.2；下表标注「—」表示公开资料未覆盖。

| 能力维度 | B1 OpenWebUI | B2 Dify | B3 Copilot Studio | B4 LibreChat | B5 Cursor | 说明 / 来源 |
| --- | --- | --- | --- | --- | --- | --- |
| 多用户与 RBAC | ✅ RBAC/角色/用户组/SSO/SCIM 2.0 | ✅ RBAC 4 角色 + 多工作区（EE） | ✅ Entra 身份/权限治理 | ✅ SSO(OAuth/SAML/LDAP)/Admin | ✅ Teams RBAC/SSO（企业版） | docs.openwebui.com/features；dify.ai；microsoft Copilot Studio；librechat.ai；cursor.com/pricing |
| 团队工作区 / 协作单元 | 部分（组 / 共享对话，非工作区） | ✅ 共享工作区 + DSL 协作导出 | ✅ Teams 频道 / 项目 Agent | 部分（Admin 配置式，非工作区） | ❌ 单用户（集中计费非协同） | openwebui features；dify blog；Ignite 2025；librechat roadmap |
| 多 Agent 编排 | ❌ 原生弱（单 AI 对话范式） | ✅ Agent 节点 + 共享会话状态 / 并行 / 重试 | ✅ Multi-Agent Orchestration | 部分（Agent Handoffs Beta） | ❌ 单 Agent（个人） | dify 53ai 文；Build 2025；librechat v0.8.1 |
| 实时通信（SSE/WS/presence） | ✅（WS 水平扩展） | ✅（实时执行可见性 / 日志） | ✅（Teams 实时） | ✅（多实例 Redis） | ✅（IDE 实时） | Slack 架构（WebSocket+Kafka 事件骨干）印证模式 |
| 协同编辑 | ❌ | ❌ | 部分（Office Agent Mode） | ❌ | ❌ | — |
| HITL / 审批 | 部分（权限 / 配额） | ✅（审批 / 变量 / 人工节点） | ✅（human oversight / 审批） | 部分 | ❌（个人） | dify blog；Build 2025 |
| 工具 / MCP | ✅ MCP Streamable HTTP | ✅ MCP 集成 + 发布 MCP Server | ✅ MCP GA（2025-05） | ✅ MCP | ✅ MCP/skills/hooks | 各产品官方文档 |
| 审计日志 | ✅（企业版审计） | ✅ 审计日志 / 活动日志 | ✅ Purview / Entra 审计 | ✅ Admin 审计 | ✅（企业版审计日志） | openwebui；dify；Ignite 2025；librechat；cursor Enterprise |
| 自托管 / 合规可控 | ✅ 完全自托管 / air-gapped / 数据驻留 | ✅ 自托管 OSS + EE | ❌ SaaS（M365 边界） | ✅ 自托管 | 部分（Enterprise on-prem inference） | 各产品部署说明 |
| 水平扩展 | ✅ Redis sessions / 多节点 | ✅（企业可扩展） | ✅（云） | ✅ Redis Leader Election | ✅（云） | openwebui features；librechat v0.8.1 |

> **Agent 框架技术使能参考（非评分标杆，归 §1.2 范围约定）**：
> - **LangGraph**：状态机模型，原生 `interrupt()` + `Command(resume=...)` 实现 HITL，检查点可恢复，条件边适合生产可靠编排（来源：syntharatechnologies blog；bestaiweb.ai）。
> - **AutoGen**：对话驱动，原生 `UserProxyAgent`（human_input_mode）将人置于环中（来源：yuzec.com；learnagent.wiki）。
> - **CrewAI**：角色 / 目标 / 任务模型，human_input 门控最终答案（HITL 非惯用），长运行可恢复性差（来源：syntharatechnologies blog）。
> - **ACP（Zed，2025-08，Apache 2.0）**：JSON-RPC 2.0 over stdio，session 管理 + `session/update` 流式 + `session/request_permission` 人机权限请求；与 MCP 互补（来源：agentic-ai.readthedocs；morphllm.com）。Hermes 已用 ACP over stdio（D1/D4）。
> - **MCP（Anthropic，2024-11，现归 Linux 基金会 AAIF）**：JSON-RPC 2.0，tools/resources/prompts/tasks 四原语，规范明确「SHOULD 始终有人在环中可拒绝工具调用」；2025-11-25 规范引入 Tasks（input_required 等状态）（来源：modelcontextprotocol.io；modelcontextprotocol.info 博客）。

---

## 3. 对比：对比矩阵与加权评分

> **四段式「对比」段**。在 §2 的事实基础上建立对比矩阵，赋予权重并打分。所有评分仅作评估，非下游裁决依据。

### 3.1 对比矩阵

> **每行权重之和 = 1.00**。评估维度与权重依据本项目核心场景（自托管团队 + 多 AI Agent 协作）设定。

| 评估维度 | 权重 | 权重理由 | B1 OpenWebUI | B2 Dify | B3 Copilot Studio | B4 LibreChat | B5 Cursor |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 场景契合度 | 0.30 | 与「真人团队 + 多 AI Agent 协作 WebUI」核心场景的匹配度，是本项目首要差异化诉求（诉求①） | 4 | 5 | 3 | 3 | 1 |
| 技术成熟度 | 0.20 | 平台稳定性、生态规模、企业落地案例，决定借鉴风险 | 5 | 5 | 5 | 4 | 5 |
| 集成难度（反向） | 0.15 | 越高分越易集成；Hermes 需在其自托管基座上扩展，集成友好度重要 | 5 | 4 | 2 | 4 | 2 |
| 成本（反向） | 0.15 | 越高分成本越低；Hermes 对标自托管范式，SaaS 订阅成本不利 | 5 | 4 | 2 | 5 | 2 |
| 合规可控性 | 0.20 | Hermes 为自托管产品（D1 §1/§17），数据驻留 / 审计 / 私有化是硬约束 | 5 | 5 | 2 | 5 | 2 |
| **加权总分** | **1.00** | — | **4.70** | **4.70** | **2.90** | **4.05** | **2.30** |

**评分标尺**：每项 1~5 分，1 = 严重不符合，3 = 基本满足但存在明显局限，5 = 完美契合。

**加权总分计算（供复核）**：
- B1 = 4×0.30 + 5×0.20 + 5×0.15 + 5×0.15 + 5×0.20 = 1.20 + 1.00 + 0.75 + 0.75 + 1.00 = **4.70**
- B2 = 5×0.30 + 5×0.20 + 4×0.15 + 4×0.15 + 5×0.20 = 1.50 + 1.00 + 0.60 + 0.60 + 1.00 = **4.70**
- B3 = 3×0.30 + 5×0.20 + 2×0.15 + 2×0.15 + 2×0.20 = 0.90 + 1.00 + 0.30 + 0.30 + 0.40 = **2.90**
- B4 = 3×0.30 + 4×0.20 + 4×0.15 + 5×0.15 + 5×0.20 = 0.90 + 0.80 + 0.60 + 0.75 + 1.00 = **4.05**
- B5 = 1×0.30 + 5×0.20 + 2×0.15 + 2×0.15 + 2×0.20 = 0.30 + 1.00 + 0.30 + 0.30 + 0.40 = **2.30**

### 3.2 评分结论

> 基于 §3.1 加权总分，形成分层结论。每层结论引用得分作为依据。结论均为「建议」。

- **优先借鉴**：**Dify（B2，4.70）+ OpenWebUI（B1，4.70）— 双核互补**。
  - 理由：Dify 在「场景契合度 5（多 Agent 编排 + 团队工作区 + 可视化 + 审计/合规最贴合）」领先；OpenWebUI 在「集成难度 5 / 成本 5 / 合规可控 5（自托管基座 + RBAC/SSO/SCIM/MCP/审计/水平扩展）」领先。两者合计覆盖 Hermes 既需「自托管基座」又需「多 Agent 编排」的核心诉求，且均为开源可自托管（合规可控 5）。二者总分并列 4.70，差异在维度侧重而非优劣，故以「双核」并列优先。
- **部分借鉴**：**LibreChat（B4，4.05）** 与 **Microsoft Copilot Studio（B3，2.90）**。
  - LibreChat 借鉴点：开源多用户 Agents / MCP / SSO / Admin Panel / Agent Handoffs 模式；不借鉴部分：其「管理员配置式」而非「工作区式」的协作模型（团队协同弱于 Dify，场景契合度仅 3）。
  - Copilot Studio 借鉴点：多 Agent 编排的「人机监督 + Entra Agent ID + Purview 审计 / 治理」治理范式（概念层，可直接映射 Hermes 的权限矩阵与审计需求）；不借鉴部分：闭源 SaaS 部署形态（合规可控 2，与 Hermes 自托管定位冲突），仅作治理范式参考。
- **不借鉴（否决）**：**Cursor（B5，2.30）**。
  - 否决理由：场景契合度 1（团队实时协同 / 圆桌 / 多 Agent 协同为 0），总分 2.30 最低；其「个人 × AI」产品范式与协作模型恰是 Hermes 应差异化于的方向。仅作为 §2.2.5 的对照锚点，说明「Hermes 区别于什么」，不作为能力借鉴对象。

### 3.3 方案组合分析

| 组合方式 | 覆盖哪些能力 | 未覆盖能力 | 组合复杂度 | 总体成本估算 |
| --- | --- | --- | --- | --- |
| **OpenWebUI 基座范式 + Dify 编排范式 + ACP/MCP 协议层 + LibreChat 开源参考 + Copilot Studio 治理范式** | 自托管基座（RBAC/SSO/SCIM/MCP/审计/水平扩展）、多用户、多 Agent 编排（共享会话状态/条件分支/并行）、实时通信、MCP 工具、审计/治理 | 真人+多 Agent 圆桌实时混合流（已有 evt:conv 基础，需增强）、协同编辑（需引入 Yjs）、澄清请求 HITL（已有 hermes:clarify 基础，需对接 ACP session/request_permission） | 中（以 Hermes 现有 ACP 调度层为骨架，借鉴为「模式」而非直接 fork） | 自托管零许可成本（OpenWebUI/Dify/LibreChat 均开源；注意 OpenWebUI License 品牌条款、Dify EE 高级特性）；工程成本主要为编排层与协同编辑自研 |

---

## 4. 建议：取舍决策支持

> **四段式「建议」段**。基于 §2 事实 + §3 对比，给出可被 `business-architect` 直接采用的建议。本节是建议而非最终裁决，最终边界由业务架构师冻结。

### 4.1 自研 / 采购 / 复用边界建议

| 能力项 | 建议方式 | 建议依据 | 候选方案 / 系统 | 关键前提 |
| --- | --- | --- | --- | --- |
| 前端 WebUI（Vue3/TS/Pinia/Naive UI） | 复用（已有底座） | D1/D3 已用 Vue3+TS+Pinia+Naive UI | 现有前端 | 需对齐 D2/D3 UI 规范（stage/admin-hero/section-card 等） |
| 多用户 / RBAC / SSO / 审计 | 复用 + 参考增强 | 已有 RBAC/governance/audit（D1/D2/D3）；OpenWebUI 有 SCIM 2.0 / 细粒度权限可作增强参考 | OpenWebUI 权限模型 | 需评估 SCIM 2.0 是否纳入 MVP |
| Agent 运行时调度层（ACP over stdio） | 复用（已有） | D1/D4 Hermes Agent via ACP；ACP 为标准（JSON-RPC/stdio，session/request_permission） | ACP 协议 + Hermes Agent | 锁定 ACP wire protocolVersion，适配层隔离 |
| 多 Agent 编排 / 圆桌 | 部分自研 + 参考 | Dify Agent 节点 / LangGraph 状态机 interrupt；Hermes 已有圆桌 WebSocket（D1/D2） | Dify 编排范式 + LangGraph HITL | 需定义编排 DSL / 状态机 |
| 协同编辑（CRDT） | 自研 / 引入 | Hermes 当前 workspace 编辑非协同（D1 §13）；Yjs 为成熟 CRDT | Yjs + y-websocket | 需评估存储 / 元数据膨胀（见 §5 R-03） |
| 实时通信（presence/typing/members_changed） | 复用（已有） | Hermes evt:conv Redis Stream 已含 typing/members_changed（D2 §7）；Slack Events API 印证模式 | 现有 evt:conv + Slack 模式参考 | 需核对 X1 流式机制冲突（§5 U-02） |
| 工具 / MCP 接入 | 复用 + 参考 | Hermes 可通过 MCP 接入工具（D4 §7）；OpenWebUI/Dify MCP 模式参考 | MCP + 现有工具 | 需治理 MCP server 权限 |
| 创新点（混合实时流 / 澄清 HITL / 协同编辑） | 部分自研 | 已有 evt:conv + hermes:clarify 基础；协同编辑为新引入 | 见 §4.3 | 见 §5 风险 |

### 4.2 MVP 范围建议

> 对用户诉求中的功能给出「是否可在 MVP 内实现」的调研侧建议。对齐用户 5 大方向与 material_digest 已确认能力。

| 功能（对齐用户诉求） | 建议 MVP？ | 理由 |
| --- | --- | --- |
| 多角色会话管理 | ✅ | OpenWebUI/LibreChat 多用户会话成熟；Hermes 已有 conversation/team 模型（D1 §9/§10） |
| 任务分配与追踪 | ✅ | Hermes 已有 Project + 任务状态（D1 §11）；Dify workflow 可参考 |
| Agent 编排（含圆桌） | ✅ | ACP + 圆桌 WebSocket 已有（D1/D2）；Dify/LangGraph 模式参考 |
| 真人–AI 权限矩阵 / HITL 澄清 | ✅ | Hermes governance + hermes:clarify 已有（D2 §7）；ACP session/request_permission 对接 |
| 审计日志 | ✅ | Hermes 已有 audit log（D1 §12）；OpenWebUI/Dify/Purview 模式印证 |
| 实时 presence/typing/members_changed | ✅ | Hermes evt:conv 已含（D2 §7）；Slack 模式印证 |
| 协同编辑（多真人实时） | ❌（完整版 / 增强） | 需引入 Yjs CRDT，MVP 先做单人 workspace 编辑；列为创新增强（§5 R-03） |
| 多 Agent 跨团队编排可视化 | ❌（完整版） | 依赖编排 DSL 成熟，MVP 先圆桌 + 顺序编排 |

### 4.3 技术栈参考建议

| 技术层 | 推荐方案 | 替代方案 | 选择理由 |
| --- | --- | --- | --- |
| 实时通信 | SSE（单 Agent）+ WebSocket（圆桌/群聊），Redis Stream 事件 | 轮询 / 纯 WebSocket 全量 | Hermes 已有 evt:conv（D2 §7）；Slack 用 WebSocket + Kafka 事件骨干印证该模式 |
| 实时事件模型 | presence / user_typing / member_joined / member_left 事件范式 | 仅消息事件 | Slack Events API（user_typing / member_joined_channel / presence_change）为业界事实标准 |
| 协同编辑 | Yjs（CRDT）+ y-websocket / y-webrtc | OT（如 ShareDB） | Yjs 网络无关、无中心真相、离线同步、26K–156K ops/s；Google Docs 用 OT（中心化），Figma 用 CRDT（离线优先） |
| Agent 协议 | ACP（over stdio，复用）+ MCP（工具/数据） | 自定义 REST agent 协议 | ACP=编辑器↔Agent 会话/流式/权限（JSON-RPC/stdio，session/request_permission HITL）；MCP=Agent↔工具/数据；两者互补（Hermes 已用 ACP） |
| HITL / 审批 | ACP session/request_permission + MCP human-in-the-loop + hermes:clarify | LangGraph interrupt/Command | ACP/MCP 协议层内置 HITL；LangGraph interrupt 为框架级范式可参考；四决策契约（approve/edit/reject/respond） |
| 多 Agent 编排 | 状态机 / 工作流（参考 Dify Agent 节点 + LangGraph 条件边 / interrupt） | 纯顺序 prompt 链 / CrewAI 角色链 | Dify 可视化 + 共享会话状态；LangGraph 条件边 / 可中断 / 检查点最适合生产可靠编排 |
| 审计 / 合规 | 既有 audit log + 不可变审计 trail（参考 OpenWebUI enterprise audit / Dify audit / Microsoft Purview） | 仅应用日志 | 团队+多 Agent 需责任归属：消息归属（user/agent 标签）+ 事件溯源（evt:conv）+ 审计绑定会话/角色 |

---

## 5. 风险与待确认项

> **四段式「风险」段**。列出调研中发现的主要风险、不确定信息、待业务架构师进一步裁决的依赖项。

### 5.1 主要风险清单

| 编号 | 风险描述 | 触发条件 | 影响范围 | 严重程度 | 缓解建议 |
| --- | --- | --- | --- | --- | --- |
| R-01 | 竞品多 Agent 编排 / 治理能力多依赖闭源或云端，自托管等价实现需额外工程 | 选 Dify/Copilot 编排范式但要求自托管 | 核心能力降级 | 高 | 基于 ACP+MCP 自研轻量编排层，复用 Dify 开源 Agent 节点思想 + LangGraph 状态机 / interrupt |
| R-02 | OpenWebUI License 为 BSD-3 修正版（保留品牌标识），若 Hermes 商业化分发需注意品牌条款 | 复用 OpenWebUI 代码 / UI | 许可合规 | 中 | 以架构 / 模式参考而非直接 fork；Hermes 为内部私有使用（D1 §17）降低风险 |
| R-03 | 实时协同编辑引入 CRDT（Yjs）带来元数据膨胀 / 离线同步复杂度 | MVP 后引入协同编辑 | 性能 / 存储 | 中 | 先做单人 workspace 编辑，协同编辑作为增强；设 compaction/TTL；参考 Yjs RLE / 列存优化 |
| R-04 | 多真人并发 + 多 Agent 混合流的一致性与责任归属难 | 圆桌 + 多 Agent + 人工混合 | 审计 / 责任 | 高 | 消息归属（user/agent 标签）、事件溯源（evt:conv）、审计绑定会话+角色；参考 Purview / Agent 365 治理 |
| R-05 | 依赖上游 Hermes Agent 的 ACP 能力与版本演进 | 上游 ACP adapter 变更 | 集成 | 中 | 锁定 ACP wire protocolVersion，适配层隔离（参考 agentclientprotocol SDK） |

### 5.2 待确认项（需主理人 / 业务方反馈）

> 调研中因外部信息不可得而暂不能确认的事实。仅以 `[待确认]` 标注，且均给出备选路径。

| 编号 | 待确认项 | 不确定性说明 | 若无法确认的备选路径 |
| --- | --- | --- | --- |
| U-01 | Microsoft Copilot Studio 与 Dify Cloud 的企业级精确定价 / 容量计费 | 公开仅 M365 Copilot Business $21/用户/月 + Copilot Studio 容量计费，无完整价目 | 以公开速率为代理，或联系厂商获取报价；不影响自托管方案选型（本项目的对标为自托管范式） |
| U-02 | X1 冲突（流式回传机制 PubSub vs Stream / 键名 chan:conv vs evt:conv）以哪个为准 | D1 §7 与 D2 §6 不一致 | 以实际代码实现为准；建议采用 XREAD + evt:conv（D2 更详细且含限流 / 重连续传，更契合多真人并发场景），需主理人 / 下游裁定并核代码 |
| U-03 | X2 代理端口（8000 vs 8001）与 X3 Redis/DB 端口及认证（文档默认 vs Docker 实际） | 配置 vs 部署差异 | 部署设计以 docker/compose 实际配置为准（D3 §9） |

### 5.3 需业务架构持续关注的依赖项

| 编号 | 依赖项 | 说明 | 建议关注阶段 |
| --- | --- | --- | --- |
| D-01 | 若选 OpenWebUI + Dify 组合借鉴，需评估自托管编排层与现有 ACP 调度层的边界（哪些自研、哪些复用） | 组合见 §3.3 | 高层架构设计 §5.2 |
| D-02 | 协同编辑（Yjs）是否纳入 MVP 或完整版，影响前端架构与存储 | 见 §4.2 / §5 R-03 | 高层架构设计 §3/§4 |
| D-03 | 审计 / 责任归属模型需嵌入安全设计 | 见 §4.3 / §5 R-04 | 安全设计 |
| D-04 | 实时通信事件模型（presence/typing/members_changed）需对齐 X1 决议 | 见 §5 U-02 | 高层架构 / 系统设计 |

---

## 6. 关键来源目录

> 集中列出全部调研所使用的公开资料、官方文档、社区仓库、分析报告等。每条来源不低于 URL 粒度，关键来源给出具体章节或段落。

**硬指标**：
- ≥ 3 条来源，覆盖每家标杆（B1–B5 全覆盖，框架参考另列）。
- 关键数据（定价、能力断言）已指定来源段落 / 页面。

| 编号 | 来源类型 | 标题 / 名称 | URL / 路径 | 相关章节 | 最后访问日期 |
| --- | --- | --- | --- | --- | --- |
| SR-01 | 官方文档 | OpenWebUI Features | https://docs.openwebui.com/features | B1, §2.1/§2.2.1/§2.3 | 2026-07-21 |
| SR-02 | 官方站点 | OpenWebUI 官网（自托管定位 / 社区规模） | https://openwebui.com/ | B1, §2.2.1 | 2026-07-21 |
| SR-03 | 开源仓库文档 | OpenWebUI README（License：BSD-3 修正版，保留品牌） | https://openaitx.github.io/projects/open-webui/open-webui/README-zh-TW.html | B1, §2.2.1 R-02 | 2026-07-21 |
| SR-04 | 官方博客 | Dify — Why A reliable visual agentic workflow matters（RBAC 4 角色 / 多工作区 / 审计 / MCP） | https://www.dify.ai/blog/why-a-reliable-visual-agentic-workflow-matters | B2, §2.2.2/§2.3 | 2026-07-21 |
| SR-05 | 官方站点 | Dify Cloud / Startup（定价 Sandbox/Professional、能力清单） | https://dify.ai/startup | B2, §2.2.2 | 2026-07-21 |
| SR-06 | 第三方分析 | 53AI — Dify 如何支持多 Agent 架构（Agent 节点 / 共享会话状态 / 不能嵌套 Agent） | https://www.53ai.com/news/dify/2025070265290.html | B2, §2.2.2/§2.3 | 2026-07-21 |
| SR-07 | 官方博客 | Microsoft 365 Blog — Build 2025 多 Agent 编排 / Copilot Tuning / Entra Agent ID / MCP | https://www.microsoft.com/en-us/microsoft-365/blog/2025/05/19/introducing-microsoft-365-copilot-tuning-multi-agent-orchestration-and-more-from-microsoft-build-2025/ | B3, §2.2.3/§2.3 | 2026-07-21 |
| SR-08 | 官方站点 | Microsoft Copilot Studio 产品页（多 Agent 协调 / 部署 M365 / Purview 治理） | https://www.microsoft.com/zh-hk/microsoft-365-copilot/microsoft-copilot-studio | B3, §2.2.3 | 2026-07-21 |
| SR-09 | 官方新闻 | Microsoft Ignite 2025（Agent 365 / $21 用户/月 / MCP GA / Teams 频道 Agent） | https://news.microsoft.com/zh-hk/2025/11/19/microsoft-ignite-2025-%E4%BB%A5-ai-%E4%BB%A3%E7%90%86%E8%88%87-copilot-%E8%B3%A6%E8%83%BD%E3%80%8Cai-%E7%82%BA%E5%85%88%E4%BC%81%E6%A5%AD%E3%80%8D/ | B3, §2.2.3/§5 U-01 | 2026-07-21 |
| SR-10 | 官方站点 | LibreChat 官网（Agents / MCP / Memory / SSO） | https://www.librechat.ai/ | B4, §2.1/§2.2.4 | 2026-07-21 |
| SR-11 | 官方博客 | LibreChat 2025 Roadmap（Admin Panel / Agentic Tooling / SSO） | https://www.librechat.ai/blog/2025-02-20_2025_roadmap | B4, §2.2.4/§2.3 | 2026-07-21 |
| SR-12 | 官方变更日志 | LibreChat v0.8.1（Agent Handoffs / Redis 分布式 Leader Election / MCP Registry） | https://librechat.ai/changelog/v0.8.1 | B4, §2.2.4/§2.3 | 2026-07-21 |
| SR-13 | 官方定价 | Cursor Pricing（Hobby/Pro $20/Pro+ $60/Ultra $200/Teams $40 用户/月/Enterprise） | https://www.cursor.com/pricing | B5, §2.2.5 | 2026-07-21 |
| SR-14 | 官方文档 | Cursor Models & Pricing（模型额度池 / 隐私模式） | https://cursor.com/zh-Hant/docs/models | B5, §2.2.5 | 2026-07-21 |
| SR-15 | 第三方分析 | Cursor 定价指南（Teams $40/用户/月、Enterprise on-prem / SCIM / 审计日志） | https://www.cursor-ide.com/blog/cursor-pricing-guide | B5, §2.2.5/§2.3 | 2026-07-21 |
| SR-16 | 标准文档 | Agent Client Protocol（ACP）规范 — session/request_permission / 与 MCP 互补 | https://agentic-ai.readthedocs.io/en/latest/Standards/agent-client-protocol/ | §2.3 框架参考 / §4.3 | 2026-07-21 |
| SR-17 | 标准概览 | Agent Client Protocol（Zed，2025-08，Apache 2.0，25+ agents） | https://www.morphllm.com/agent-client-protocol | §2.3 / §4.3 | 2026-07-21 |
| SR-18 | 标准文档 | Model Context Protocol（MCP）规范 — tools/resources/prompts，human-in-the-loop SHOULD | https://modelcontextprotocol.io/specification | §2.3 框架参考 / §4.3 | 2026-07-21 |
| SR-19 | 官方博客 | MCP 一周年（2025-11-25 规范、Tasks 原语、10,000+ servers、归 Linux 基金会 AAIF） | https://modelcontextprotocol.info/zh-cn/blog/first-mcp-anniversary/ | §2.3 / §4.3 | 2026-07-21 |
| SR-20 | 社区 wiki | Human-in-the-Loop（LangGraph interrupt / AutoGen UserProxyAgent / CrewAI human_input） | https://learnagent.wiki/agent/cards/human-in-the-loop | §2.3 框架参考 | 2026-07-21 |
| SR-21 | 技术博客 | Adding Human Approval Gates（LangGraph/AutoGen/CrewAI/OpenAI 四决策契约） | https://www.bestaiweb.ai/how-to-add-human-approval-gates-to-agents-with-langgraph-autogen-and-crewai-in-2026 | §2.3 / §4.3 | 2026-07-21 |
| SR-22 | 开发者文档 | Slack Events API（member_joined_channel / member_left_channel 等） | https://api.slack.com/events?query=group | §2.3 / §4.3 | 2026-07-21 |
| SR-23 | 开发者文档 | Slack user_typing event | https://docs.slack.dev/reference/events/user_typing | §2.3 / §4.3 | 2026-07-21 |
| SR-24 | 技术博客 | How Slack Works（WebSocket 网关 + Kafka 事件骨干、presence/typing 架构） | https://rishijeet.github.io/blog/how-slack-works | §2.3 / §4.3 | 2026-07-21 |
| SR-25 | 官方文档 | Yjs Introduction（CRDT、网络无关、无中心真相、YATA） | https://beta.yjs.dev/docs/introduction | §4.3 / §5 R-03 | 2026-07-21 |
| SR-26 | 技术博客 | CRDT Guide（Yjs vs Automerge、CRDT vs OT、Google Docs/Figma/Notion 用例） | https://velt.dev/blog/crdt-implementation-guide-conflict-free-apps | §4.3 / §5 R-03 | 2026-07-21 |
| SR-27 | 研究博客 | CRDTs and Real-Time Collaboration（Yjs 26K–156K ops/s、Figma 切 CRDT、tombstone 问题） | https://zylos.ai/research/2026-01-29-crdt-real-time-collaboration | §4.3 / §5 R-03 | 2026-07-21 |
| SR-28 | 上游资料 | material_digest.md（G1 已通过，D1–D4 摘要与 X1–X3 冲突） | /Users/caotinghui/Downloads/hermes-python/.workbuddy/output/material_digest.md | 全局（事实基线） | 2026-07-21 |
| SR-29 | 第三方分析 | Cognition / Devin（MultiDevin 经理+工人、VPC 部署、Interactive Planning、Slack/Teams 集成；$20/月 Core + ACU） | https://svtr.ai/articles/cognition | §1 Q4 / 创新点参考 | 2026-07-21 |

---

## 7. 硬指标清单

> 汇总本模板所有章节的硬指标，供自动校验与人工审核使用。

| 章节 | 硬指标项 | 当前状态 | 备注 |
| --- | --- | --- | --- |
| §1 | 调研问题已收敛为 ≥ 3 条可执行问题 | ✅ | 收敛为 Q1–Q4（4 条） |
| §2.1 | 标杆系统 ≥ 3 家，含 ≥ 1 家头部 SaaS | ✅ | B1–B5 共 5 家；头部 SaaS：Dify/B3 Copilot Studio/B5 Cursor |
| §2.1 | 标杆系统 ≥ 1 家开源或自研代表 | ✅ | B1 OpenWebUI/B2 Dify/B4 LibreChat 均为开源 |
| §2.2 | 每家标杆有独立详述卡片 | ✅ | B1–B5 共 5 张 10 维度卡片 |
| §2.3 | 关键能力横向事实无遗漏 | ✅ | 10 能力维度 × 5 标杆 + 框架参考 |
| §3.1 | 对比矩阵含 5 维度 + 权重 + 评分 | ✅ | 权重之和 = 1.00（0.30+0.20+0.15+0.15+0.20） |
| §3.2 | 评分结论含优先/部分/不借鉴三层 | ✅ | 优先(Dify+OpenWebUI)/部分(LibreChat+Copilot Studio)/不借鉴(Cursor) |
| §4.1 | 自研/采购/复用边界有明确建议 | ✅ | 8 项能力边界建议 |
| §4.2 | MVP 范围建议与用户诉求对齐 | ✅ | 对齐用户 5 方向 + material_digest 能力 |
| §5.1 | 主要风险 ≥ 3 条，有缓解建议 | ✅ | R-01–R-05 共 5 条，均带缓解 |
| §6 | 关键来源可追溯（URL / 章节） | ✅ | SR-01–SR-29，覆盖全部标杆 |
| 全文 | 明确区分事实 / 推断 / 建议 / 风险 | ✅ | §2 事实(含置信度)、§3 对比、§4 建议、§5 风险 |
| 全文 | 不存在编造来源或占位符 | ✅ | 全文无残留尖括号占位、日期占位、示例前缀；严禁标记未使用；仅 §5.2 使用待确认标记 |

---

## 8. 自检报告（中间确认协议 §2.4）

> 按中间确认协议 §2.4，在 §1 收敛后、§2.1 标杆名单后、§3.1 权重设定前、§5.2 待确认项整理时各做一次自检（先按 §2.1 判定，再按 §2.3 反向验证 3 问）。本报告为最终回传的追溯材料。

### 8.1 自检节点 1 — §1 调研问题收敛后

- **§2.1 方案分歧判定**：用户诉求「重点 A」列举了产品级竞品与 Agent 框架，存在「仅产品级评分 vs 产品+框架均评分」两种理解。**但**：用户明确将研究焦点定为「产品差异化定位」（诉求①），且「重点 B/C」要求分析「业界做法与开源实现」「架构与能力的开源/商用实现」——框架据此归入技术参考是简明的、被简报本身支撑的释义，并非需用户二选一的 contested fork；下游业务架构师仍可自由改变该范围。条件（3）「用户/上游未对该决策点做明确选择」成立，但条件（1）的"≥2 种合理方案均影响下游"在本语境下弱（框架参考 vs 产品评分的差异不改变 Hermes 自研/复用边界结论，仅改变 §2.3 呈现形态）。**判定：未命中阻塞触发**。
- **§2.3 反向验证 3 问**：
  - Q1（3 个月后被推翻的返工成本）：返工范围 = §2.3 框架呈现段落 + §4.3 技术栈参考的"框架引用"部分；切换成本 = 低（仅调整文档呈现与引用归类，不影响 Hermes 代码/架构）。证据：框架仅作为"技术使能参考"文字出现，未进入评分矩阵。
  - Q2（用户/客户/监管可感知？）：感知不到。证据：标杆范围约定是内部研究裁量，不进入产品功能/交互/合同/合规任何用户可见面。
  - Q3（与用户原始诉求显式提及的能力一致？）：用户诉求①原文「对比现有『个人 × AI』协作 WebUI 产品……明确……独特价值与架构需求差异」+ 重点 A 原文「盘点并对比两类产品——(a) 个人×AI……(b) 团队/多Agent 协作平台：Dify、LangGraph/CrewAI/AutoGen、Microsoft Copilot……」。用户将框架列在"(b) 团队/多Agent 协作平台"下，未要求将其作为独立评分标杆；本研究将其作为"协作模型技术使能参考"仍覆盖该诉求。证据：已直接引用用户原文。
- **结论**：不发起 `[中间确认]`；范围约定写入 §1.2 供下游裁决。

### 8.2 自检节点 2 — §2.1 标杆名单后

- **§2.1 方案分歧判定**：候选 5 家（OpenWebUI/Dify/Copilot Studio/LibreChat/Cursor）覆盖了用户列举的两类产品（a 个人×AI：OpenWebUI/LibreChat/Cursor；b 团队/多Agent：Dify/Copilot Studio），并含用户列举的 Devin 作为创新点参考（SR-29）。数量与行业/地域范围清晰（均为全球通用英文产品，无地域歧义）。**判定：未命中阻塞触发**。
- **§2.3 反向验证 3 问**：
  - Q1：若推翻标杆名单，返工范围 = §2.1 表 + §2.2 卡片 + §3.1 矩阵列；切换成本 = 中（需补/换标杆的卡片与评分），但不影响下游架构边界（下游以证据为输入自行裁决）。
  - Q2：感知不到（内部研究清单，非用户可见）。
  - Q3：与诉求一致。证据：用户重点 A 列举 ChatGPT/Claude/Cursor/OpenWebUI/LibreChat（个人×AI）与 Dify/Copilot/Notion/Devin/Slack/Teams（团队/多Agent）；本研究选取其中具代表性且可获取公开文档者（OpenWebUI/LibreChat/Cursor/Dify/Copilot Studio），并以 Devin 作创新参考，覆盖诉求列举的品类。
- **结论**：不发起 `[中间确认]`。

### 8.3 自检节点 3 — §3.1 权重设定前

- **§2.1 方案分歧判定**：默认权重（场景契合度 0.30 / 技术成熟度 0.20 / 集成难度 0.15 / 成本 0.15 / 合规可控 0.20）是否适用？本项目核心场景为「自托管团队+多Agent 协作 WebUI」，场景契合度（0.30）与合规可控性（0.20，Hermes 为自托管产品 D1 §1/§17）为本项目最高权重维度，符合项目实际；不存在"权重重设会反转排名"的情形（Dify 与 OpenWebUI 并列 4.70 的成因是维度侧重差异，非权重敏感）。**判定：未命中阻塞触发**。
- **§2.3 反向验证 3 问**：
  - Q1：返工范围 = §3.1 权重行 + §3.2 计算；切换成本 = 极低（重算加权，不涉及代码/架构）。
  - Q2：感知不到（权重为内部分析参数）。
  - Q3：与诉求一致。证据：用户诉求①③④均指向"团队协同场景匹配 / 自托管架构 / 合规可控"，权重分配与之一致；用户未显式指定权重。
- **结论**：不发起 `[中间确认]`；权重理由写入 §3.1。

### 8.4 自检节点 4 — §5.2 待确认项整理时

- **§2.1 方案分歧判定**：待确认项 U-01（竞品定价）、U-02（X1 流式机制冲突）、U-03（X2/X3 端口冲突）均为"外部信息不可得 / 上游资料冲突"，需主理人或下游裁定，但均已有明确备选路径（§5.2 第三列），不阻塞报告交付；其中 U-02/U-03 直接源自 material_digest 已记录的 X1–X3 冲突（上游未裁决，符合 G1 不裁决冲突的约定）。**判定：未命中阻塞触发**（非方案分歧，属信息待确认，已用 `[待确认]` + 备选路径处理）。
- **§2.3 反向验证 3 问**：
  - Q1：U-02/U-03 若推翻，返工范围 = §5 U-02/U-03 + 下游架构相关章节；切换成本 = 中（需主理人/下游裁定后修订流式机制与端口约定），但本报告已给出建议默认（XREAD+evt:conv、以 docker/compose 为准）降低返工。
  - Q2：U-02（流式机制）与 U-03（端口）**用户/客户可感知**——流式机制影响客户端实时体验，端口影响部署。证据：evt:conv/presence 直接决定前端实时交互（D2 §7/§8）；端口决定部署可达性（D3 §9）。
  - Q3：与诉求一致（待确认项不替代用户决策，仅标注信息缺口）。
- **结论**：U-02/U-03 涉及用户可感知的部署/交互面，但属"上游资料冲突待裁决"而非"本研究方案分歧"，且已给备选路径，故不按 §2.1 发起阻塞；将其列于 §5.2 与 §5.3（D-04），由主理人/下游在 G3 阶段裁定。

### 8.5 总体声明

- 四次自检均**未命中**中间确认协议的阻塞触发条件；研究中未静默选择争议方案，所有范围/权重/标杆选择均在 §1.2、§3.1 显式说明理由。
- 全部结论（§3.2 三层评分、§4.1 边界、§4.2 MVP、§4.3 技术栈）均标注为「建议」，保留 `business-architect` 在 G3 阶段的完整裁决权；研究打分最优不构成「已冻结」输入（依用户主理人指令与协议 §2.1 澄清）。
