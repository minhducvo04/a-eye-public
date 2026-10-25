#!/bin/bash
# Unattended overnight run on the GPU box (WSL), 2026-10-24. Every stage has a clock deadline; the app's models
# are restored and warmed at the end no matter what (and again by the 07:25 Windows task "A-eye restore").
# Results: $PC_WORK_DIR/aeye-night (status.txt, *.jsonl, logs). Never touches the app's model tags.
set -u
: "${PC_WORK_DIR:?Set PC_WORK_DIR to the WSL work directory}"
: "${PC_WINDOWS_MODELS_DIR:?Set PC_WINDOWS_MODELS_DIR to the Windows model directory}"
: "${OLLAMA_URL:?Set OLLAMA_URL to your local Ollama service URL}"
: "${OLLAMA_EXE:?Set OLLAMA_EXE to the WSL path of ollama.exe}"
: "${WSL_SETUP_SCRIPT:?Set WSL_SETUP_SCRIPT to your local setup script}"
export PATH=$HOME/.local/bin:$PATH OLLAMA_URL PYTHONUNBUFFERED=1
export UV_HTTP_TIMEOUT=900 UV_CONCURRENT_DOWNLOADS=3
REPO=~/aeye/a-eye; R="$PC_WORK_DIR/aeye-night"; mkdir -p $R
APP=~/aeye/appvenv/bin/python; ML=~/aeye/.venv/bin/python
OL="$OLLAMA_EXE"
cd $REPO
at() { date -d "today $1" +%s; }
left() { echo $(( $(at $1) - $(date +%s) )); }
say() { echo "$(date +%H:%M:%S) $*" | tee -a $R/status.txt; }
unload_all() {
  for m in $(curl -s $OLLAMA_URL/api/ps | python3 -c "import json,sys;[print(m['name']) for m in json.load(sys.stdin)['models']]"); do
    curl -s $OLLAMA_URL/api/generate -d "{\"model\":\"$m\",\"keep_alive\":0}" > /dev/null
  done
}
restore() {
  say "restore: unloading and warming the app's models"
  unload_all; sleep 3
  for m in qwen3.5:9b-q4_K_M qwen2.5vl:7b-q4_K_M; do
    curl -s $OLLAMA_URL/api/chat -d "{\"model\":\"$m\",\"stream\":false,\"keep_alive\":\"30m\",\"think\":false,\"options\":{\"temperature\":0,\"num_ctx\":4096,\"num_predict\":1},\"messages\":[{\"role\":\"user\",\"content\":\"Reply OK.\"}]}" > /dev/null
  done
  curl -s $OLLAMA_URL/api/embed -d '{"model":"embeddinggemma:300m","input":"warm up","keep_alive":"24h"}' > /dev/null
  curl -s $OLLAMA_URL/api/chat -d '{"model":"qwen3.6:35b-a3b-q4_K_M","stream":false,"keep_alive":"2h","think":false,"options":{"num_ctx":4096,"num_gpu":0,"num_predict":1},"messages":[{"role":"user","content":"Reply OK."}]}' > /dev/null
  curl -s $OLLAMA_URL/api/ps > $R/ps_after_restore.json
  say "restore done: $(python3 -c "import json;print([m['name'] for m in json.load(open('$R/ps_after_restore.json'))['models']])")"
}
trap 'restore; say "NIGHT END (trap)"' EXIT
say "night start"

# 1. Light app venv (repo requirements) for generation and evals.
[ -x $APP ] || { uv venv --allow-existing --python 3.12 ~/aeye/appvenv && uv pip install --python $APP -r requirements.txt >> $R/setup.log 2>&1; }
say "app venv ready: $($APP -c 'import httpx;print("ok")' 2>&1)"

# 2. Background prep: training venv (torch, unsloth, speech) then the Qwen3.5-9B base weights.
( bash "$WSL_SETUP_SCRIPT" >> $R/setup.log 2>&1; say "ml venv: $($ML -c 'import torch;print(torch.__version__, torch.cuda.is_available())' 2>&1 | tail -1)"
  uvx --from "huggingface_hub[cli]" hf download Qwen/Qwen3.5-9B --local-dir ~/aeye/models/Qwen3.5-9B >> $R/download.log 2>&1
  du -sh ~/aeye/models/Qwen3.5-9B > ~/aeye/weights.done; say "weights: $(cat ~/aeye/weights.done)" ) &
PREP=$!

# 3. Teacher data (gemma4:31b, thinking on) until 04:40, and the CPU-4 tiny tier in parallel (CPU only).
( for m in qwen2.5vl:3b moondream:1.8b qwen3-vl:2b; do
    EVAL_BACKENDS_OUT=$REPO/eval/tiers/out timeout $(left 04:40) $APP -m eval.backends.run --backends ollama-cpu4:$m --reps 1 --warm > /dev/null 2>&1
    grep -q "\"model\": \"$m\", \"device\": \"cpu4\"" eval/tiers/out/ground.jsonl 2>/dev/null || \
      timeout $(left 04:40) $APP -m eval.tiers.vlm_ground --model $m --device cpu4 --out eval/tiers/out/ground.jsonl > /dev/null 2>&1
    curl -s $OLLAMA_URL/api/generate -d "{\"model\":\"$m\",\"keep_alive\":0}" > /dev/null
  done; say "cpu4 tiny tier done" ) &
timeout $(left 04:40) $APP -m eval.brain.distill gen --scenes ~/aeye/scenes.jsonl --teacher gemma4:31b-it-q4_K_M --think \
  --out ~/aeye/train_raw.jsonl >> $R/gen.log 2>&1
python3 -c "
import json
rows=[json.loads(l) for l in open('$HOME/aeye/train_raw.jsonl')]
keep=[r for r in rows if r.get('messages')]
open('$HOME/aeye/train.jsonl','w').write(''.join(json.dumps(r)+'\n' for r in keep))
print(len(keep))" > $R/train_count.txt
cp ~/aeye/train_raw.jsonl $R/
say "teacher data: $(cat $R/train_count.txt) training rows"

# 4. Wait for the venv and weights (until 05:20), then train with the whole GPU until 06:20.
while [ ! -f ~/aeye/weights.done ] && [ $(left 05:20) -gt 0 ]; do sleep 60; done
TRAINED=0
if [ -f ~/aeye/weights.done ]; then
  unload_all; sleep 5
  ( for job in "whisper tiny.en cpu" "whisper base.en cpu" "moonshine moonshine-ai/moonshine-tiny cpu" "moonshine moonshine-ai/moonshine-base cpu"; do
      timeout 900 $ML eval/tiers/speech.py --cases eval/brain/cases.json --out $R/speech.jsonl --voice-dir ~/aeye/piper --only "$job" >> $R/speech.log 2>&1
    done ) &
  say "training start"
  timeout $(left 06:20) $ML eval/brain/train_talker.py --base ~/aeye/models/Qwen3.5-9B --data ~/aeye/train.jsonl \
    --out ~/aeye/out/qwen-aeye-talk-9b --epochs 2 > $R/train.log 2>&1 && TRAINED=1
  say "training exit: trained=$TRAINED ($(grep -E '^train ' $R/train.log | tail -1 | cut -c1-200))"
else
  say "SKIP training: base weights not downloaded by 05:20 (Wi-Fi about 4 MB/s)"
fi

# 5. Export to a NEW Ollama tag (never the app's qwen3.5:9b-q4_K_M).
if [ $TRAINED = 1 ] && [ $(left 06:50) -gt 0 ]; then
  rm -rf "$PC_WORK_DIR/models/qwen-aeye-talk-9b" && cp -r ~/aeye/out/qwen-aeye-talk-9b "$PC_WORK_DIR/models/"
  "$OL" show qwen3.5:9b-q4_K_M --modelfile | grep -v '^FROM ' > "$PC_WORK_DIR/models/Modelfile.talk.tmp"
  { echo "FROM $PC_WINDOWS_MODELS_DIR\qwen-aeye-talk-9b"; cat "$PC_WORK_DIR/models/Modelfile.talk.tmp"; } > "$PC_WORK_DIR/models/Modelfile.talk"
  timeout $(left 06:50) "$OL" create qwen-aeye-talk:9b --quantize q4_K_M -f "$PC_WINDOWS_MODELS_DIR\Modelfile.talk" > $R/export.log 2>&1
  say "export exit $? ($(tail -1 $R/export.log | cut -c1-160))"
fi

# 6. Evaluate the student against the base from the box (same place, same GPU), plus the hazard probe.
if curl -s $OLLAMA_URL/api/tags | grep -q qwen-aeye-talk:9b && [ $(left 07:05) -gt 0 ]; then
  unload_all
  for m in qwen3.5:9b-q4_K_M qwen-aeye-talk:9b; do
    BRAIN_RUN_TAG=+box BRAIN_NUM_GPU=999 BRAIN_NUM_THREAD=auto timeout $(left 07:05) $APP -m eval.brain.run --backends local:$m --only talker >> $R/student_eval.log 2>&1
    timeout 300 $APP -m eval.brain.distill safety --model $m --out $R/safety_probe.jsonl >> $R/student_eval.log 2>&1
  done
  cp eval/brain/out/*+box.jsonl $R/ 2>/dev/null
  say "student eval done: $(grep 'safety misses' $R/student_eval.log | tr '\n' ' ')"
fi

# 7. Tiny tier on the GPU (until 07:15), then judge the tiny full-turn rows with gemma4:31b (the harness judge).
for m in moondream:1.8b qwen2.5vl:3b qwen3-vl:2b; do
  [ $(left 07:12) -gt 0 ] || break
  EVAL_BACKENDS_OUT=$REPO/eval/tiers/out timeout $(left 07:12) $APP -m eval.backends.run --backends ollama:$m --reps 1 --warm > /dev/null 2>&1
  timeout $(left 07:12) $APP -m eval.tiers.vlm_ground --model $m --device gpu --out eval/tiers/out/ground.jsonl > /dev/null 2>&1
  curl -s $OLLAMA_URL/api/generate -d "{\"model\":\"$m\",\"keep_alive\":0}" > /dev/null
done
[ $(left 07:18) -gt 0 ] && EVAL_BACKENDS_OUT=$REPO/eval/tiers/out timeout $(left 07:18) $APP -m eval.backends.run --judge > $R/tiers_table.txt 2>&1
cp eval/tiers/out/* $R/ 2>/dev/null
say "tiny tier copied"
# trap runs restore()
