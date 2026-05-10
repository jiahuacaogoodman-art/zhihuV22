# 智护银伴 v21 趋势设计版说明

这一版不再走“简化降噪”的方向，而是按答辩展示、产品路演和高完成度 UI 作品集的标准做复杂化设计。

## 视觉方向

1. 医疗科技 OS
   - 深色空间背景、动态 Aurora 光晕、科技网格、玻璃拟态面板。
   - 管理端 Hero 被设计成“AI Nursing Command Center”，更像系统中枢而不是普通表单页。

2. Bento 信息层级
   - Hero 下方新增三枚指挥条：Local RAG、Task Engine、Bed-side 闭环。
   - 卡片加入渐变描边、局部高光、光标聚焦和轻微 3D 视差。

3. 高级动效
   - 本地 particles.js 使用多色粒子与连接线。
   - `trend-ui.js` 注入动态环境光球、卡片 spotlight、Hero 能力概览。
   - 保留 GSAP / Typed / AOS / SweetAlert2 的本地资源调用。

4. 护工端升级
   - 左侧列表改成深色玻璃侧栏。
   - 详情页和任务卡维持高科技面板质感，强调护理事件处理闭环。

## 本地化资源

所有前端增强依赖均放在 `static/vendor/` 或 `static/trend-ui.*`，页面不需要访问 CDN。

## 未改动范围

没有改动后端 API、数据库结构、AI 任务卡生成逻辑、护理事件归档逻辑。
