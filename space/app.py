"""Photo consultation using the identified checkpoint; no benchmark data or scoring."""
import os
import time
import spaces  # Must be imported before torch for ZeroGPU CUDA handling.
import gradio as gr
import torch
from PIL import Image, ImageOps
from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration

BASE_ID = "Qwen/Qwen3.5-9B"
BASE_REVISION = "c202236235762e1c871ad0ccb60c8ee5ba337b9a"
MODEL_ID = os.environ.get("MODEL_ID", BASE_ID)
MODEL_REVISION = os.environ.get("MODEL_REVISION", BASE_REVISION if MODEL_ID == BASE_ID else "")
MODEL_LABEL = os.environ.get("MODEL_LABEL", "Qwen3.5-9B (Thinking) · Official base model" if MODEL_ID == BASE_ID else "")
MODEL_NOTE = "The current live app uses the official base model; the fine-tuned **Ours** release will follow." if MODEL_ID == BASE_ID else "The active checkpoint is identified above."
if not MODEL_REVISION or not MODEL_LABEL:
    raise RuntimeError("A custom model requires MODEL_REVISION and MODEL_LABEL.")

Image.MAX_IMAGE_PIXELS = 25_000_000
processor = AutoProcessor.from_pretrained(MODEL_ID, revision=MODEL_REVISION, trust_remote_code=False)
model = Qwen3_5ForConditionalGeneration.from_pretrained(
    MODEL_ID, revision=MODEL_REVISION, dtype=torch.bfloat16,
    device_map="cuda", attn_implementation="sdpa", trust_remote_code=False,
).eval()

SYSTEM = """You are a plant photo consultation assistant. Answer in the user's language.
Use only the uploaded photo, the user's information, and your general knowledge.
Distinguish visible observations from possible explanations. State uncertainty when
the photo cannot establish a diagnosis. Do not invent sensor readings, prior photos,
laboratory tests, treatments already performed, reference retrieval, or tool results.
For diagnosis, explain the visible evidence, likely possibilities and information
that could distinguish them. For care, give practical next steps appropriate to
the available evidence, their conditions and what to monitor. If critical context
is missing, ask concise follow-up questions. You cannot browse or run tests.
Provide a clear natural-language final answer, not a benchmark JSON report."""


def split_response(raw):
    """The thinking chat template opens <think>; only </think> starts the answer."""
    if "</think>" not in raw:
        return raw.removeprefix("<think>").strip(), "", False
    thinking, answer = raw.split("</think>", 1)
    return thinking.removeprefix("<think>").strip(), answer.strip(), bool(answer.strip())


@spaces.GPU(duration=120)
def respond(photo, question, task):
    if photo is None:
        raise gr.Error("Please upload a plant photo.")
    question = (question or "").strip()
    if not question:
        raise gr.Error("Please enter your question.")
    if len(question) > 4000:
        raise gr.Error("Please keep the question within 4,000 characters.")
    if task not in {"P3 · Diagnosis", "P4 · Care"}:
        raise gr.Error("Choose diagnosis or care.")
    if photo.width * photo.height > 25_000_000:
        raise gr.Error("Please upload a photo smaller than 25 megapixels.")
    photo = ImageOps.exif_transpose(photo).convert("RGB")
    photo.thumbnail((1280, 1280))
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": [
            {"type": "image", "image": photo},
            {"type": "text", "text": f"Task: {task}\nQuestion: {question}"},
        ]},
    ]
    started = time.monotonic()
    try:
        inputs = processor.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True,
            enable_thinking=True, return_dict=True, return_tensors="pt",
        ).to(model.device)
        with torch.inference_mode():
            output = model.generate(
                **inputs, max_new_tokens=4096, max_time=95,
                do_sample=True, temperature=0.6, top_p=0.95, top_k=20,
            )
        tokens = output[0, inputs["input_ids"].shape[-1]:]
        raw = processor.decode(tokens, skip_special_tokens=True)
        thinking, answer, complete = split_response(raw)
        status = f"{MODEL_LABEL} · {len(tokens)} output tokens · {time.monotonic()-started:.1f}s"
        if not complete:
            status += " · Incomplete: the model did not finish a final answer within this request."
            answer = "No final answer was completed. You can shorten the question and try again. The generated thinking is shown separately below."
        return answer, thinking or "No separate thinking text was returned.", status
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        raise gr.Error("GPU memory was insufficient. Please try a smaller image or a shorter question.") from None


with gr.Blocks(title="PlantAgentBench · Try the Agent", delete_cache=(3600, 3600)) as demo:
    gr.Markdown("## Your plant. Your question.")
    gr.Markdown(f"**{MODEL_LABEL}**\n\nUpload a photo for diagnosis or care. {MODEL_NOTE}")
    with gr.Row():
        with gr.Column():
            photo = gr.Image(label="Plant photo", type="pil", sources=["upload"], image_mode="RGB", height=300)
            task = gr.Radio(["P3 · Diagnosis", "P4 · Care"], value="P3 · Diagnosis", label="What would you like help with?")
            question = gr.Textbox(label="Your question", placeholder="Why are these leaves turning yellow? The plant is indoors and I water it twice a week.", lines=4, max_lines=10)
            submit = gr.Button("Ask the model", variant="primary")
        with gr.Column():
            status = gr.Textbox(label="Request status", value="Ready · Shared GPU requests may queue.", interactive=False)
            answer = gr.Textbox(label="Response", lines=14, interactive=False)
            with gr.Accordion("Model thinking", open=False):
                thinking = gr.Textbox(label="Generated thinking", lines=12, interactive=False)
    gr.ClearButton([photo, question, answer, thinking, status])
    gr.Markdown("Photo consultation uses your image and question. It does not access the benchmark's evidence tools or compute a benchmark score. Photos and questions are sent to this Hugging Face Space for processing.")
    submit.click(respond, [photo, question, task], [answer, thinking, status], api_name="respond", concurrency_limit=1)

demo.queue(max_size=8, default_concurrency_limit=1).launch(max_file_size="15mb")
