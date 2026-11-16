#!/usr/bin/env bash
# Effective settings of the completed 16-A100 / 4-node training run.
# Launch once per node after your scheduler allocates the requested resources.
set -euo pipefail
: "${MODEL_PATH:?Path to the pinned Qwen3.5-9B checkpoint}"
: "${DATA_DIR:?Directory containing train.jsonl and val.jsonl}"
: "${OUTPUT_DIR:?New checkpoint output directory}"
: "${NNODES:?Original run used 4 nodes}"
: "${NODE_RANK:?Set uniquely to 0..NNODES-1}"
: "${MASTER_ADDR:?Set the coordinator address inside your allocation}"
: "${MASTER_PORT:?Set a free coordinator port}"
export NNODES NODE_RANK MASTER_ADDR MASTER_PORT
export NPROC_PER_NODE="${NPROC_PER_NODE:-4}"
export IMAGE_MIN_TOKEN_NUM=64 IMAGE_MAX_TOKEN_NUM=16384
recipe_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec swift sft \
  --model "$MODEL_PATH" --model_type qwen3_5 --template qwen3_5 \
  --dataset "$DATA_DIR/train.jsonl" --val_dataset "$DATA_DIR/val.jsonl" --split_dataset_ratio 0 \
  --tuner_type full --torch_dtype bfloat16 --attn_impl flash_attn \
  --freeze_vit true --freeze_aligner true \
  --max_length 65536 --truncation_strategy delete --loss_scale ignore_think_prefix \
  --padding_free true --packing false --sequence_parallel_size 4 \
  --deepspeed "$recipe_dir/zero3_optim_offload.json" --gradient_checkpointing true --use_liger_kernel true \
  --per_device_train_batch_size 1 --per_device_eval_batch_size 1 --gradient_accumulation_steps 4 \
  --learning_rate 1e-5 --lr_scheduler_type cosine --warmup_ratio 0.03 --num_train_epochs 2 \
  --optim adamw_torch_fused --adam_beta1 0.9 --adam_beta2 0.95 --adam_epsilon 1e-8 \
  --weight_decay 0.1 --max_grad_norm 1.0 --seed 42 \
  --eval_strategy steps --eval_steps 100 --save_strategy steps --save_steps 100 --save_total_limit 3 \
  --logging_steps 1 --dataloader_num_workers 4 --dataset_num_proc 8 \
  --report_to tensorboard --output_dir "$OUTPUT_DIR"
