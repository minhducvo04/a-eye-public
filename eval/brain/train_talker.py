"""Phase B: LoRA the Qwen3.5-9B talker on open-teacher data (eval/brain/distill.py), then merge to bf16.

Runs on the GPU box in WSL with the whole GPU (the app's models unloaded). Language layers only, vision frozen,
loss on the assistant reply only. The merged weights become a NEW Ollama tag (qwen-aeye-talk:9b); the app's
qwen3.5:9b-q4_K_M is never touched.

  python train_talker.py --base ~/aeye/models/Qwen3.5-9B --data train.jsonl --out ~/aeye/out/talk --steps 10   # smoke
  python train_talker.py --base ~/aeye/models/Qwen3.5-9B --data train.jsonl --out ~/aeye/out/talk --epochs 2
"""
import argparse
import json
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--data', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--epochs', type=float, default=2)
    ap.add_argument('--steps', type=int, default=-1)
    ap.add_argument('--lr', type=float, default=1e-4)
    ap.add_argument('--no-merge', action='store_true')
    a = ap.parse_args()

    from unsloth import FastModel
    from unsloth.chat_templates import train_on_responses_only
    import torch
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    t0 = time.time()
    model, tok = FastModel.from_pretrained(a.base, max_seq_length=2048, load_in_4bit=False, load_in_16bit=True,
                                           full_finetuning=False)
    model = FastModel.get_peft_model(model, r=16, lora_alpha=16, lora_dropout=0, bias='none',
                                     finetune_vision_layers=False, finetune_language_layers=True,
                                     finetune_attention_modules=True, finetune_mlp_modules=True,
                                     use_gradient_checkpointing='unsloth', random_state=7)
    text_tok = getattr(tok, 'tokenizer', tok)
    rows = [json.loads(line) for line in open(a.data)]
    rows = [r for r in rows if r.get('messages')]

    def render(messages):
        return text_tok.apply_chat_template(messages, tokenize=False, enable_thinking=False)

    ds = Dataset.from_list([{'text': render(r['messages'])} for r in rows])
    print('examples', len(ds), 'load_s', round(time.time() - t0), flush=True)
    cfg = SFTConfig(output_dir=a.out + '-ckpt', dataset_text_field='text', per_device_train_batch_size=1,
                    gradient_accumulation_steps=8, num_train_epochs=a.epochs, max_steps=a.steps,
                    learning_rate=a.lr, lr_scheduler_type='cosine', warmup_ratio=0.05, logging_steps=5,
                    bf16=True, optim='adamw_8bit', weight_decay=0.01, max_length=2048, save_strategy='no',
                    report_to='none', seed=7)
    trainer = SFTTrainer(model=model, processing_class=text_tok, train_dataset=ds, args=cfg)
    trainer = train_on_responses_only(trainer, instruction_part='<|im_start|>user\n',
                                      response_part='<|im_start|>assistant\n')
    stats = trainer.train()
    print('train', json.dumps(stats.metrics), 'peak_gb', round(torch.cuda.max_memory_allocated() / 2**30, 1),
          flush=True)
    model.save_pretrained(a.out + '-lora')
    if not a.no_merge:
        model.save_pretrained_merged(a.out, tok, save_method='merged_16bit')
        print('merged to', a.out, flush=True)


if __name__ == '__main__':
    main()
