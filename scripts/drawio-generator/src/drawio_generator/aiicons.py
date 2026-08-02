"""AI 品牌图标库 — 98 项 AI 产品/厂商/模型品牌 icon 映射

图标通过 drawio `shape=image;image=URL` 嵌入，SVG 端 fallback 为品牌色块 + 文字。

索引格式: {
  brand_key: {
    "name": str,                 # 中文名
    "aliases": [str],            # 别名
    "category": str,             # 大类 (llm / inference / infra / framework / vision / audio / agent / tool / data / cloud / search)
    "color": str,                # 品牌主色（fallback 填充）
    "drawio_image_url": str,     # drawio 可访问的 CDN URL（.png/.svg）
    "logo_svg": str or None,     # 极简 SVG path 或 None（有则嵌入 defs 用 svg:symbol 引用）
  }
}
"""
from copy import deepcopy
from difflib import SequenceMatcher
import re

# 预编译正则，用于 _normalize 高频调用
_NORMALIZE_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")

# 官方 SVG CDN 前缀：品牌 logo 走 simpleicons (MIT 协议)
_SIMPLEICONS = "https://cdn.simpleicons.org/"
_SIMPLEICONS_CC = "#FFFFFF"  # 大多数品牌 logo 用白色背景

AIICONS = {
    # ===== 大语言模型 (LLM) =====
    "openai": {
        "name": "OpenAI / GPT",
        "aliases": ["gpt", "gpt4", "gpt-4", "gpt-4o", "chatgpt", "o1", "o3", "openaigpt"],
        "category": "llm",
        "color": "#000000",
        "drawio_image_url": f"{_SIMPLEICONS}openai/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "claude": {
        "name": "Anthropic Claude",
        "aliases": ["anthropic", "claude3", "claude 3", "sonnet", "opus", "haiku", "claude4"],
        "category": "llm",
        "color": "#D0752C",
        "drawio_image_url": f"{_SIMPLEICONS}anthropic/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "gemini": {
        "name": "Google Gemini",
        "aliases": ["google ai", "bard", "gemma", "palm", "google gemini", "google"],
        "category": "llm",
        "color": "#4285F4",
        "drawio_image_url": f"{_SIMPLEICONS}google/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "deepseek": {
        "name": "DeepSeek",
        "aliases": ["deep seek", "deepseek-v3", "deepseek-r1", "reasoner"],
        "category": "llm",
        "color": "#5B68F4",
        "drawio_image_url": f"{_SIMPLEICONS}deepseek/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "qwen": {
        "name": "通义千问 (Qwen)",
        "aliases": ["tongyi", "qwen2", "qwen-2", "千问", "alibaba", "阿里"],
        "category": "llm",
        "color": "#FF6A00",
        "drawio_image_url": f"{_SIMPLEICONS}alibaba/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "doubao": {
        "name": "豆包 (Doubao)",
        "aliases": ["doubao", "bytedance", "字节", "volc", "火山", "volcengine"],
        "category": "llm",
        "color": "#3D7EFF",
        "drawio_image_url": f"{_SIMPLEICONS}bytedance/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "kimi": {
        "name": "Kimi / 月之暗面",
        "aliases": ["moonshot", "kimi ai", "moon shot"],
        "category": "llm",
        "color": "#1B2A4E",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "glm": {
        "name": "智谱 GLM",
        "aliases": ["zhipu", "chatglm", "glm-4", "清智", "tsinghua"],
        "category": "llm",
        "color": "#0060FF",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "llama": {
        "name": "Meta Llama",
        "aliases": ["llama3", "llama-3", "meta", "meta ai", "facebook ai"],
        "category": "llm",
        "color": "#0081FB",
        "drawio_image_url": f"{_SIMPLEICONS}meta/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "mistral": {
        "name": "Mistral AI",
        "aliases": ["mixtral", "mistral-large"],
        "category": "llm",
        "color": "#FF7000",
        "drawio_image_url": f"{_SIMPLEICONS}mistral/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "cohere": {
        "name": "Cohere",
        "aliases": ["command r", "command r+"],
        "category": "llm",
        "color": "#333333",
        "drawio_image_url": f"{_SIMPLEICONS}cohere/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "groq": {
        "name": "Groq",
        "aliases": ["lpu", "groqcloud"],
        "category": "llm",
        "color": "#F55036",
        "drawio_image_url": f"{_SIMPLEICONS}groq/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "xai": {
        "name": "xAI / Grok",
        "aliases": ["grok", "elon", "x ai"],
        "category": "llm",
        "color": "#000000",
        "drawio_image_url": f"{_SIMPLEICONS}x/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "sensetime": {
        "name": "日日新 SenseNova",
        "aliases": ["sensenova", "商汤", "sense time", "日日新"],
        "category": "llm",
        "color": "#1461FF",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "minimax": {
        "name": "MiniMax / 海螺",
        "aliases": ["minimax", "海螺", "hai"],
        "category": "llm",
        "color": "#8C55FF",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "abab": {
        "name": "MiniMax abab",
        "aliases": ["abab 6.5", "abab7", "minimax model"],
        "category": "llm",
        "color": "#8C55FF",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "stepfun": {
        "name": "阶跃星辰 StepFun",
        "aliases": ["step 1", "step 2", "stepfun ai", "阶跃"],
        "category": "llm",
        "color": "#0A59FF",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "codestral": {
        "name": "Codestral",
        "aliases": ["code llama", "代码模型", "coding llm"],
        "category": "llm",
        "color": "#FF7000",
        "drawio_image_url": f"{_SIMPLEICONS}mistral/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },

    # ===== 推理服务 (inference) =====
    "vllm": {
        "name": "vLLM",
        "aliases": ["vllm", "fast inference", "paged attention"],
        "category": "inference",
        "color": "#7B68EE",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "sglang": {
        "name": "SGLang",
        "aliases": ["sgl", "srt", "structured generation"],
        "category": "inference",
        "color": "#008888",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "tensorrt": {
        "name": "TensorRT-LLM",
        "aliases": ["trt", "tensorrt", "nvidia inference", "trt-llm"],
        "category": "inference",
        "color": "#76B900",
        "drawio_image_url": f"{_SIMPLEICONS}nvidia/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "ollama": {
        "name": "Ollama",
        "aliases": ["local llm", "本地部署"],
        "category": "inference",
        "color": "#000000",
        "drawio_image_url": f"{_SIMPLEICONS}ollama/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "lmdeploy": {
        "name": "LMDeploy",
        "aliases": ["lm deploy", "opencompass", "internlm inference"],
        "category": "inference",
        "color": "#2F80ED",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "textgen": {
        "name": "text-generation-webui",
        "aliases": ["oobabooga", "textgenwebui", "ooba"],
        "category": "inference",
        "color": "#1C1C1C",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "openrouter": {
        "name": "OpenRouter",
        "aliases": ["or", "聚合路由"],
        "category": "inference",
        "color": "#000000",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "togetherai": {
        "name": "Together AI",
        "aliases": ["together", "together.ai"],
        "category": "inference",
        "color": "#FF5A4F",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "fireworks": {
        "name": "Fireworks AI",
        "aliases": ["fireworksai", "fireworks.ai"],
        "category": "inference",
        "color": "#FF661A",
        "drawio_image_url": None,
        "logo_svg": None,
    },

    # ===== 基础设施 / 芯片 =====
    "nvidia": {
        "name": "NVIDIA GPU",
        "aliases": ["gpu", "a100", "h100", "h800", "nvlink", "cuda"],
        "category": "infra",
        "color": "#76B900",
        "drawio_image_url": f"{_SIMPLEICONS}nvidia/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "amd": {
        "name": "AMD GPU",
        "aliases": ["rocm", "mi300", "amd gpu"],
        "category": "infra",
        "color": "#ED1C24",
        "drawio_image_url": f"{_SIMPLEICONS}amd/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "cuda": {
        "name": "CUDA",
        "aliases": ["cuda toolkit", "nvcc"],
        "category": "infra",
        "color": "#76B900",
        "drawio_image_url": f"{_SIMPLEICONS}nvidia/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "kubernetes": {
        "name": "Kubernetes",
        "aliases": ["k8s", "kubectl", "容器编排"],
        "category": "infra",
        "color": "#326CE5",
        "drawio_image_url": f"{_SIMPLEICONS}kubernetes/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "docker": {
        "name": "Docker",
        "aliases": ["container", "镜像"],
        "category": "infra",
        "color": "#2496ED",
        "drawio_image_url": f"{_SIMPLEICONS}docker/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "ray": {
        "name": "Ray",
        "aliases": ["ray cluster", "anyscale", "分布式训练"],
        "category": "infra",
        "color": "#00A3E0",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "slurm": {
        "name": "Slurm",
        "aliases": ["slurm cluster", "作业调度"],
        "category": "infra",
        "color": "#2C5282",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "triton": {
        "name": "Triton Inference Server",
        "aliases": ["triton server", "nvtriton"],
        "category": "infra",
        "color": "#76B900",
        "drawio_image_url": f"{_SIMPLEICONS}nvidia/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },

    # ===== 框架 / 训练 =====
    "pytorch": {
        "name": "PyTorch",
        "aliases": ["torch", "ml framework"],
        "category": "framework",
        "color": "#EE4C2C",
        "drawio_image_url": f"{_SIMPLEICONS}pytorch/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "tensorflow": {
        "name": "TensorFlow",
        "aliases": ["tf", "tf2", "keras"],
        "category": "framework",
        "color": "#FF6F00",
        "drawio_image_url": f"{_SIMPLEICONS}tensorflow/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "jax": {
        "name": "JAX",
        "aliases": ["jaxlib", "flax", "tpu"],
        "category": "framework",
        "color": "#49B882",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "transformers": {
        "name": "Transformers / 🤗",
        "aliases": ["huggingface", "hf", "hugging face", "transformers lib"],
        "category": "framework",
        "color": "#FF9D00",
        "drawio_image_url": f"{_SIMPLEICONS}huggingface/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "datasets": {
        "name": "🤗 Datasets",
        "aliases": ["hf datasets", "hub datasets"],
        "category": "framework",
        "color": "#FF9D00",
        "drawio_image_url": f"{_SIMPLEICONS}huggingface/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "diffusers": {
        "name": "Diffusers",
        "aliases": ["stable diffusion", "sd", "lora", "文生图"],
        "category": "framework",
        "color": "#FF9D00",
        "drawio_image_url": f"{_SIMPLEICONS}huggingface/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "megatron": {
        "name": "Megatron-LM",
        "aliases": ["megatron", "nlp pretrain", "pipeline parallel"],
        "category": "framework",
        "color": "#1976D2",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "deepspeed": {
        "name": "DeepSpeed",
        "aliases": ["ds", "microsoft deepspeed", "zero optimizer"],
        "category": "framework",
        "color": "#0078D4",
        "drawio_image_url": f"{_SIMPLEICONS}microsoft/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "fairseq": {
        "name": "FairSeq",
        "aliases": ["meta fairseq", "seq2seq"],
        "category": "framework",
        "color": "#0081FB",
        "drawio_image_url": f"{_SIMPLEICONS}meta/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "lightning": {
        "name": "Lightning (PyTorch)",
        "aliases": ["pl", "pytorch lightning", "lightning fabric"],
        "category": "framework",
        "color": "#792EE5",
        "drawio_image_url": f"{_SIMPLEICONS}lightning/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },

    # ===== 视觉 / 多模态 =====
    "stable-diffusion": {
        "name": "Stable Diffusion",
        "aliases": ["sdxl", "sd 3", "stable diffusion 3", "stability ai"],
        "category": "vision",
        "color": "#666666",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "midjourney": {
        "name": "Midjourney",
        "aliases": ["mj", "mj ai", "文生图"],
        "category": "vision",
        "color": "#FFFFFF",
        "drawio_image_url": f"{_SIMPLEICONS}midjourney/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "dall-e": {
        "name": "DALL·E",
        "aliases": ["dalle", "dalle 3", "dalle3"],
        "category": "vision",
        "color": "#000000",
        "drawio_image_url": f"{_SIMPLEICONS}openai/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "flux": {
        "name": "Flux",
        "aliases": ["black forest labs", "flux dev", "flux schnell"],
        "category": "vision",
        "color": "#333333",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "whisper": {
        "name": "Whisper / 语音转写",
        "aliases": ["whisper ai", "asr", "语音识别"],
        "category": "audio",
        "color": "#000000",
        "drawio_image_url": f"{_SIMPLEICONS}openai/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "elevenlabs": {
        "name": "ElevenLabs / TTS",
        "aliases": ["11labs", "eleven labs", "tts", "语音合成"],
        "category": "audio",
        "color": "#000000",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "sora": {
        "name": "Sora 视频生成",
        "aliases": ["openai sora", "文生视频", "video"],
        "category": "vision",
        "color": "#000000",
        "drawio_image_url": f"{_SIMPLEICONS}openai/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "video-gen": {
        "name": "通用视频生成",
        "aliases": ["vidu", "kling", "可灵", "vidu", "sora 替代"],
        "category": "vision",
        "color": "#3D5AFE",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "o3": {
        "name": "Omicron 3D / 3D 生成",
        "aliases": ["3d gen", "mesh gen", "3d model"],
        "category": "vision",
        "color": "#4A90A4",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "sam": {
        "name": "SAM 分割",
        "aliases": ["segment anything", "meta sam", "图像分割"],
        "category": "vision",
        "color": "#0081FB",
        "drawio_image_url": f"{_SIMPLEICONS}meta/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "yolo": {
        "name": "YOLO 检测",
        "aliases": ["yolo v8", "yolo v10", "ultralytics", "目标检测"],
        "category": "vision",
        "color": "#F00000",
        "drawio_image_url": None,
        "logo_svg": None,
    },

    # ===== Agent / 智能体 =====
    "langchain": {
        "name": "LangChain",
        "aliases": ["lc", "lang graph", "langgraph"],
        "category": "agent",
        "color": "#00C7B7",
        "drawio_image_url": f"{_SIMPLEICONS}langchain/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "llamaindex": {
        "name": "LlamaIndex",
        "aliases": ["llama index", "gpt index", "rag 框架"],
        "category": "agent",
        "color": "#2A4F7B",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "crewai": {
        "name": "CrewAI",
        "aliases": ["crew", "多智能体"],
        "category": "agent",
        "color": "#1C1C1C",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "autogen": {
        "name": "AutoGen",
        "aliases": ["microsoft autogen", "multi-agent"],
        "category": "agent",
        "color": "#0078D4",
        "drawio_image_url": f"{_SIMPLEICONS}microsoft/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "dify": {
        "name": "Dify",
        "aliases": ["langflow 竞品", "dify llm", "llm app"],
        "category": "agent",
        "color": "#1D5DCC",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "flowise": {
        "name": "Flowise",
        "aliases": ["langflow ui", "drag drop llm"],
        "category": "agent",
        "color": "#2E9CCA",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "meta-gpt": {
        "name": "MetaGPT",
        "aliases": ["metagpt", "多角色 agent"],
        "category": "agent",
        "color": "#5A67D8",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "openthinker": {
        "name": "OpenThinker / 通用 Agent",
        "aliases": ["thinker", "agent runtime"],
        "category": "agent",
        "color": "#4F46E5",
        "drawio_image_url": None,
        "logo_svg": None,
    },

    # ===== 工具 =====
    "langsmith": {
        "name": "LangSmith 观测",
        "aliases": ["langsmith", "lc observability", "tracing"],
        "category": "tool",
        "color": "#00C7B7",
        "drawio_image_url": f"{_SIMPLEICONS}langchain/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "wandb": {
        "name": "Weights & Biases",
        "aliases": ["wandb", "mlops", "experiment tracking"],
        "category": "tool",
        "color": "#FFBE00",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "mlflow": {
        "name": "MLflow",
        "aliases": ["ml flow", "model registry"],
        "category": "tool",
        "color": "#0194E2",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "kubeflow": {
        "name": "Kubeflow",
        "aliases": ["kf", "ml pipeline k8s"],
        "category": "tool",
        "color": "#326CE5",
        "drawio_image_url": f"{_SIMPLEICONS}kubernetes/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "airflow": {
        "name": "Apache Airflow",
        "aliases": ["airflow", "workflow", "dag"],
        "category": "tool",
        "color": "#017CEE",
        "drawio_image_url": f"{_SIMPLEICONS}apacheairflow/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "fastapi": {
        "name": "FastAPI",
        "aliases": ["fast api", "uvicorn", "python server"],
        "category": "tool",
        "color": "#009688",
        "drawio_image_url": f"{_SIMPLEICONS}fastapi/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "gradio": {
        "name": "Gradio",
        "aliases": ["gradio ui", "ml demo"],
        "category": "tool",
        "color": "#000000",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "streamlit": {
        "name": "Streamlit",
        "aliases": ["streamlit ui", "数据展示"],
        "category": "tool",
        "color": "#FF4B4B",
        "drawio_image_url": f"{_SIMPLEICONS}streamlit/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "postman": {
        "name": "Postman",
        "aliases": ["api 测试"],
        "category": "tool",
        "color": "#FF6C37",
        "drawio_image_url": f"{_SIMPLEICONS}postman/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },

    # ===== 数据 / RAG =====
    "milvus": {
        "name": "Milvus 向量库",
        "aliases": ["zilliz", "向量数据库", "vector db"],
        "category": "data",
        "color": "#00B140",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "qdrant": {
        "name": "Qdrant 向量库",
        "aliases": ["qdrant", "向量搜索"],
        "category": "data",
        "color": "#E65100",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "pgvector": {
        "name": "pgvector",
        "aliases": ["postgres vector", "pg vector"],
        "category": "data",
        "color": "#336791",
        "drawio_image_url": f"{_SIMPLEICONS}postgresql/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "chroma": {
        "name": "Chroma",
        "aliases": ["chroma db", "本地向量库"],
        "category": "data",
        "color": "#7D7D7D",
        "drawio_image_url": f"{_SIMPLEICONS}chrome/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "elasticsearch": {
        "name": "Elasticsearch",
        "aliases": ["es", "lucene", "检索"],
        "category": "data",
        "color": "#005571",
        "drawio_image_url": f"{_SIMPLEICONS}elasticsearch/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "redis": {
        "name": "Redis",
        "aliases": ["cache", "缓存", "redis search"],
        "category": "data",
        "color": "#DC382D",
        "drawio_image_url": f"{_SIMPLEICONS}redis/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "postgres": {
        "name": "PostgreSQL",
        "aliases": ["pg", "psql", "关系数据库"],
        "category": "data",
        "color": "#336791",
        "drawio_image_url": f"{_SIMPLEICONS}postgresql/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "mysql": {
        "name": "MySQL",
        "aliases": ["mariadb"],
        "category": "data",
        "color": "#4479A1",
        "drawio_image_url": f"{_SIMPLEICONS}mysql/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "mongodb": {
        "name": "MongoDB",
        "aliases": ["mongo", "文档数据库"],
        "category": "data",
        "color": "#47A248",
        "drawio_image_url": f"{_SIMPLEICONS}mongodb/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "s3": {
        "name": "S3 / 对象存储",
        "aliases": ["oss", "minio", "对象存储", "bucket"],
        "category": "data",
        "color": "#FF9900",
        "drawio_image_url": f"{_SIMPLEICONS}amazons3/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "kafka": {
        "name": "Kafka",
        "aliases": ["streaming", "消息", "event stream"],
        "category": "data",
        "color": "#231F20",
        "drawio_image_url": f"{_SIMPLEICONS}apachekafka/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "spark": {
        "name": "Apache Spark",
        "aliases": ["pyspark", "大数据"],
        "category": "data",
        "color": "#E25A1C",
        "drawio_image_url": f"{_SIMPLEICONS}apachespark/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },

    # ===== 云 / 部署平台 =====
    "aws": {
        "name": "AWS",
        "aliases": ["amazon aws", "bedrock", "sagemaker"],
        "category": "cloud",
        "color": "#FF9900",
        "drawio_image_url": f"{_SIMPLEICONS}amazonwebservices/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "azure": {
        "name": "Microsoft Azure",
        "aliases": ["azure ai", "azure openai", "aoai", "microsoft cloud"],
        "category": "cloud",
        "color": "#0078D4",
        "drawio_image_url": f"{_SIMPLEICONS}microsoftazure/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "gcp": {
        "name": "Google Cloud",
        "aliases": ["gcp", "vertex ai", "google cloud"],
        "category": "cloud",
        "color": "#4285F4",
        "drawio_image_url": f"{_SIMPLEICONS}googlecloud/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "aliyun": {
        "name": "阿里云",
        "aliases": ["alibaba cloud", "百炼", "dashscope"],
        "category": "cloud",
        "color": "#FF6A00",
        "drawio_image_url": f"{_SIMPLEICONS}alibabacloud/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "tencentcloud": {
        "name": "腾讯云",
        "aliases": ["tencent cloud", "腾讯云 hunyuan", "hunyuan"],
        "category": "cloud",
        "color": "#00A4FF",
        "drawio_image_url": f"{_SIMPLEICONS}tencentqq/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "vercel": {
        "name": "Vercel",
        "aliases": ["vercel ai", "next.js", "前端托管"],
        "category": "cloud",
        "color": "#000000",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "cloudflare": {
        "name": "Cloudflare",
        "aliases": ["cf", "cloudflare workers ai"],
        "category": "cloud",
        "color": "#F38020",
        "drawio_image_url": f"{_SIMPLEICONS}cloudflare/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },

    # ===== 搜索 =====
    "perplexity": {
        "name": "Perplexity AI",
        "aliases": ["pplx", "perplexity ai search"],
        "category": "search",
        "color": "#1B1B1B",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "brave": {
        "name": "Brave Search",
        "aliases": ["brave search api", "web search"],
        "category": "search",
        "color": "#FB542B",
        "drawio_image_url": f"{_SIMPLEICONS}brave/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "bing": {
        "name": "Bing / Copilot",
        "aliases": ["bing search", "microsoft copilot", "phind 替代"],
        "category": "search",
        "color": "#008373",
        "drawio_image_url": f"{_SIMPLEICONS}bing/{_SIMPLEICONS_CC}",
        "logo_svg": None,
    },
    "tavily": {
        "name": "Tavily AI 搜索",
        "aliases": ["tavily search", "rag search"],
        "category": "search",
        "color": "#111111",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "exa": {
        "name": "Exa.ai 语义搜索",
        "aliases": ["exa ai", "semantic search", "metaphor systems"],
        "category": "search",
        "color": "#5E17EB",
        "drawio_image_url": None,
        "logo_svg": None,
    },
    "searxng": {
        "name": "SearXNG",
        "aliases": ["searx", "元搜索"],
        "category": "search",
        "color": "#3D3D3D",
        "drawio_image_url": None,
        "logo_svg": None,
    },
}

# ===== 类别索引 =====
ICON_CATEGORIES = sorted({v["category"] for v in AIICONS.values()})


def list_icons(category=None):
    """列出所有 icon，按类别过滤（返回深拷贝，防止修改原始数据）"""
    if not category:
        return deepcopy(AIICONS)
    return {k: deepcopy(v) for k, v in AIICONS.items() if v["category"] == category}


def get_icon(brand_key):
    """按 key 取 brand 信息（返回深拷贝，防止修改原始数据）"""
    item = AIICONS.get(brand_key)
    return deepcopy(item) if item is not None else None


def _normalize(s):
    return _NORMALIZE_RE.sub(" ", s.lower()).strip()


def search_icon(query, limit=5, threshold=0.35):
    """模糊搜索 AI icon，支持别名、中英文、类别"""
    if not query:
        return []
    q = _normalize(query)
    if not q:
        return []
    tokens = q.split()

    scored = []
    for key, info in AIICONS.items():
        key_n = _normalize(key)
        name_n = _normalize(info["name"])
        aliases_n = {_normalize(a) for a in info["aliases"]}
        cat_n = _normalize(info["category"])

        score = 0.0
        # 精确匹配
        if q == key_n:
            score += 3.0
        elif q in key_n:
            score += 1.0
        if q == name_n:
            score += 3.0
        elif q in name_n:
            score += 1.2
        if q in cat_n:
            score += 0.3

        # token 命中
        for tok in tokens:
            if not tok:
                continue
            if tok in aliases_n:
                score += 1.5
            if tok in name_n:
                score += 0.7
            if tok in key_n:
                score += 0.6
            if tok in cat_n:
                score += 0.2

        # 相似度兜底
        combined = f"{key_n} {name_n} {' '.join(aliases_n)} {cat_n}"
        sim = max(
            SequenceMatcher(None, q, combined[:200]).ratio(),
            SequenceMatcher(None, q, name_n).ratio(),
            SequenceMatcher(None, q, key_n).ratio(),
        )
        if sim > threshold:
            score += sim * 0.5

        if score > 0:
            scored.append((score, key))

    seen = set()
    result = []
    for score, key in sorted(scored, key=lambda x: (-x[0], x[1])):
        if key in seen:
            continue
        seen.add(key)
        result.append((key, deepcopy(AIICONS[key]), round(score, 3)))
        if len(result) >= limit:
            break
    return result


def summary():
    """统计"""
    total = len(AIICONS)
    per_cat = {c: sum(1 for v in AIICONS.values() if v["category"] == c) for c in ICON_CATEGORIES}
    return {
        "total": total,
        "categories": ICON_CATEGORIES,
        "per_category": per_cat,
    }
