---
name: codex-second-opinion
description: >-
  Request an independent cross-model adversarial code review or deep root-cause second opinion
  from a Codex model in strict read-only sandbox (`preset="review"` or `preset="debug"`).
  Trigger before merging significant changes or when debugging a stubborn issue.
---

# Codex Second Opinion（跨模型独立审查与会诊）

在完成重要功能、准备合并分支或遇到复杂疑难 Bug 时，请 Codex 中的另一个模型家族以**严格只读（`read-only`）**身份提供独立视角审查，避免单一模型盲区。

## 两种常用模式

1. **合并前代码审查（`preset="review"`，默认 `read-only` + `medium`）**：
   - 在 `prompt` 中给出待审分支/文件范围、业务目标与关注重点（正确性、边界条件、安全与并发、测试覆盖），要求对方仅输出结构化问题清单。
2. **疑难根因会诊（`preset="debug"`，默认 `read-only` + `xhigh`）**：
   - 当主线排查陷入僵局或存在多个竞争假设时，把完整现象、复现命令、已排除假设与相关文件交给推理强度更高的 Codex 模型做独立根因推演。

## 分级与输出要求

在 `prompt` 中要求 Codex 按以下规范报告：
- 每条发现按 **`Critical` / `Important` / `Minor`** 分级；
- 必须附带精确的 **`file:line`** 位置、触发条件与最小可验证证据；
- 严禁修改任何文件（由 `mode="read-only"` 强制保证）。

## 主 Agent 甄别原则（禁止审查套审查）

1. **技术甄别而非盲从**：逐条核实 Codex 指出的 `file:line` 与复现逻辑，有道理则采纳修复，误报则明确说明原因并忽略。
2. **禁止无限套娃审查**：一般只对真正新发现的高风险改动追加单点复核，避免对已确认通过的代码循环互相审查消耗时间和上下文。
3. **最终责任在主 Agent**：采纳修改后必须亲自验证 diff 与测试通过再结束任务。
