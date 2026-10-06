# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目状态

新建的空项目（Aftersales-agent，售后 agent）。尚无代码、构建、lint 或测试命令；技术栈确定并落地后，在此补充常用命令（构建、lint、全量测试、运行单个测试）和整体架构说明。

- 远程仓库：https://github.com/codelformat/Aftersales-agent （默认分支 `main`，GitHub CLI `gh` 已登录）

## 分工规则：Claude 规划，Codex 编码

- **Claude Code 负责**：理解需求、阅读代码、plan 阶段的思考与方案设计、拆分任务、审查 Codex 的产出（读 diff、跑测试、核对是否符合方案）、git 提交与推送。
- **Codex 负责**：所有实际的代码编写与修改。Claude 不直接用 Edit/Write 改业务代码（CLAUDE.md、文档等非代码文件除外）。
- **调用方式**：模型 `gpt-6.1-sol`，推理强度 `high`：

  ```bash
  codex exec -C /Users/harry/Aftersales-agent \
    -m gpt-6.1-sol -c model_reasoning_effort="high" \
    -s workspace-write \
    "<任务描述>"
  ```

- **给 Codex 的任务描述要自包含**：目标、涉及的文件/模块、接口约定、验收标准（需要通过的测试或命令）、不要改动的范围。Codex 看不到本次对话上下文，方案中的关键决策必须写进 prompt。
- 大任务按 plan 拆成多个小步骤依次交给 Codex，每步完成后由 Claude 审查验证，再进入下一步；发现问题时把具体问题和修改要求反馈给 Codex 重做，而不是自己直接改代码。
