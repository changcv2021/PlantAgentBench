# PlantAgentBench

本仓库提供植物 Agent 环境、正式评分器、数据构建代码和 Thinking–Action SFT 复现材料。

[在线案例](https://changcv2021.github.io/PlantAgentBench/) ·
[Core-3K](https://huggingface.co/datasets/benchmarkanon/PlantAgentBench-Core3K) ·
[Full](https://huggingface.co/datasets/benchmarkanon/PlantAgentBench-Full) · [English](README.md)

Full 包含 **318,760 道 Agent 任务**，另有 **5,830 道组件任务**；Core-3K 是冻结的 3,000 题评测集合，是 Full 的子集。五类任务分别是生长巡检、环境调查、主动诊断、护理处置和连续随访。

## 使用

```bash
git clone https://github.com/changcv2021/PlantAgentBench.git
cd PlantAgentBench
python -m pip install -e '.[download,report]'
plantagentbench download --dataset core --output data/core
plantagentbench inspect --dataset data/core
```

自行启动多模态模型服务，服务模型名称须与配置一致。先检查少量任务：

```bash
plantagentbench evaluate --dataset data/core \
  --config configs/qwen35-9b-thinking.json \
  --api-base http://127.0.0.1:8000/v1 --output runs/qwen-smoke --limit 5
plantagentbench score --dataset data/core --run runs/qwen-smoke
plantagentbench summarize --runs runs/qwen-smoke --output outputs/qwen-smoke
```

删去 `--limit 5` 并使用新的目录可测评全部 Core。Full 通过 `download --dataset full --revision <HF提交号>` 下载。大型下载、推理、评分和训练应在合适的计算资源中运行。

模型只能接收环境公开的观察和工具；Gold 由独立评分命令读取。输出包括任务成功率、里程碑进度、格式有效率、内容正确性与覆盖率、证据支持、合法工具使用、证据获取和查询成本，以及 M1–M3 热图。

## 配置

- `qwen35-raw.json`：JSON 动作，非 Thinking，4,096 输出 token，保留原测评采样设置。
- `qwen35-9b-thinking.json`：原生工具调用，Thinking，32,768 输出 token。Base/Ours 使用相同设置、不同服务权重和运行目录。
- `api-native.example.json`：兼容 Chat Completions 的 API 模板；凭据从环境变量读取，服务商额外参数按其支持情况填写。
- `training/`：实际语言侧全参数 SFT 设置，冻结视觉模块，含 6,965 条合格轨迹分布。

运行器保存响应与交互轨迹并支持续跑，不因格式或答案错误重新抽样。默认遇到基础设施错误停止；`--on-error continue` 可保留异常并继续。异常与缺失结果单独统计，不冒充正常完成的任务。运行器没有美元额度自动控制，付费 API 应在服务商侧设置额度并先验证小样本。

本次发布不包含完整教师轨迹和微调权重。构建代码保留真实规则，仍依赖原始登记清单及 canonical/feature 输入；直接测评只需冻结数据。训练前按 [隔离说明](research_docs/ISOLATION.md) 排除 Core 及关联病例与证据。

详见 [测评](research_docs/EVALUATION.md)、[构建](research_docs/CONSTRUCTION.md)、[训练](training/README.md) 和 [演示说明](DEMO_zh.md)。代码 MIT；数据与图像按上游各自许可使用。
