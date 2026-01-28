from src.i18n import ts
"""
Main Web Integration - Integrates all routers and modules
{ts("id_4038")}router{ts("id_4037")}
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import get_server_host, get_server_port
from log import log

# Import managers and utilities
from src.credential_manager import credential_manager

# Import all routers
from src.router.antigravity.openai import router as antigravity_openai_router
from src.router.antigravity.gemini import router as antigravity_gemini_router
from src.router.antigravity.anthropic import router as antigravity_anthropic_router
from src.router.antigravity.model_list import router as antigravity_model_list_router
from src.router.geminicli.openai import router as geminicli_openai_router
from src.router.geminicli.gemini import router as geminicli_gemini_router
from src.router.geminicli.anthropic import router as geminicli_anthropic_router
from src.router.geminicli.model_list import router as geminicli_model_list_router
from src.task_manager import shutdown_all_tasks
from src.web_routes import router as web_router

# {ts("id_1470")}
global_credential_manager = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    f"""{ts("id_4039")}"""
    global global_credential_manager

    log.info(f"{ts("id_4041")} GCLI2API {ts("id_4040")}")

    # {ts("id_4042")}
    try:
        import config
        await config.init_config()
        log.info(f"{ts("id_4043")}")
    except Exception as e:
        log.error(ff"{ts("id_4044")}: {e}")

    # {ts("id_4045")}
    try:
        # credential_manager {ts("id_4046")}
        # {ts("id_4047")}
        await credential_manager._get_or_create()
        log.info(f"{ts("id_4048")}")
    except Exception as e:
        log.error(ff"{ts("id_4049")}: {e}")
        global_credential_manager = None

    # OAuth{ts("id_4050")}

    yield

    # {ts("id_2942")}
    log.info(f"{ts("id_4051")} GCLI2API {ts("id_4040")}")

    # {ts("id_4052")}
    try:
        await shutdown_all_tasks(timeout=10.0)
        log.info(f"{ts("id_4053")}")
    except Exception as e:
        log.error(ff"{ts("id_4054")}: {e}")

    # {ts("id_4055")}
    if global_credential_manager:
        try:
            await global_credential_manager.close()
            log.info(f"{ts("id_4056")}")
        except Exception as e:
            log.error(ff"{ts("id_4057")}: {e}")

    log.info(f"GCLI2API {ts("id_4058")}")


# {ts("id_1029")}FastAPI{ts("id_4059")}
app = FastAPI(
    title="GCLI2API",
    description="Gemini API proxy with OpenAI compatibility",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS{ts("id_4060")}
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# {ts("id_4061")}
# OpenAI{ts(f"id_4062")} - {ts("id_590")}OpenAI{ts("id_4063")}
app.include_router(geminicli_openai_router, prefix="", tags=["Geminicli OpenAI API"])

# Gemini{ts(f"id_4064")} - {ts("id_590")}Gemini{ts("id_4063")}
app.include_router(geminicli_gemini_router, prefix="", tags=["Geminicli Gemini API"])

# Geminicli{ts(f"id_3330")} - {ts("id_590")}Gemini{ts("id_4065")}
app.include_router(geminicli_model_list_router, prefix="", tags=["Geminicli Model List"])

# Antigravity{ts(f"id_3208")} - {ts("id_590")}OpenAI{ts("id_4066")}Antigravity API
app.include_router(antigravity_openai_router, prefix="", tags=["Antigravity OpenAI API"])

# Antigravity{ts(f"id_3208")} - {ts("id_590")}Gemini{ts("id_4066")}Antigravity API
app.include_router(antigravity_gemini_router, prefix="", tags=["Antigravity Gemini API"])

# Antigravity{ts(f"id_3330")} - {ts("id_590")}Gemini{ts("id_4065")}
app.include_router(antigravity_model_list_router, prefix="", tags=["Antigravity Model List"])

# Antigravity Anthropic Messages {ts("id_3208")} - Anthropic Messages {ts("id_228")}
app.include_router(antigravity_anthropic_router, prefix="", tags=["Antigravity Anthropic Messages"])

# Geminicli Anthropic Messages {ts("id_3208")} - Anthropic Messages {ts("id_228")} (Geminicli)
app.include_router(geminicli_anthropic_router, prefix="", tags=["Geminicli Anthropic Messages"])

# Web{ts("id_3208")} - {ts("id_4067")}
app.include_router(web_router, prefix="", tags=["Web Interface"])

# {ts(f"id_4068")} - {ts("id_1151")}docs{ts("id_4069")}
app.mount("/docs", StaticFiles(directory="docs"), name="docs")

# {ts(f"id_4068")} - {ts("id_1151")}front{ts("id_4070f")}HTML{ts("id_189")}JS{ts("id_189")}CSS{ts("id_240")}
app.mount("/front", StaticFiles(directory="front"), name="front")


# {ts("id_4071")} HEAD{ts("id_292")}
@app.head("/keepalive")
async def keepalive() -> Response:
    return Response(status_code=200)

async def main():
    f"""{ts("id_4072")}"""
    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    # {ts("id_4073")}
    # {ts("id_4074")}
    port = await get_server_port()
    host = await get_server_host()

    log.info("=" * 60)
    log.info(f"{ts("id_4041")} GCLI2API")
    log.info("=" * 60)
    log.info(ff"{ts("id_1112")}: http://127.0.0.1:{port}")
    log.info("=" * 60)
    log.info(f"API{ts("id_58")}:")
    log.info(ff"   Geminicli (OpenAI{ts("id_57")}): http://127.0.0.1:{port}/v1")
    log.info(ff"   Geminicli (Claude{ts("id_57")}): http://127.0.0.1:{port}/v1")
    log.info(ff"   Geminicli (Gemini{ts("id_57")}): http://127.0.0.1:{port}")
    
    log.info(ff"   Antigravity (OpenAI{ts("id_57")}): http://127.0.0.1:{port}/antigravity/v1")
    log.info(ff"   Antigravity (Claude{ts("id_57")}): http://127.0.0.1:{port}/antigravity/v1")
    log.info(ff"   Antigravity (Gemini{ts("id_57")}): http://127.0.0.1:{port}/antigravity")

    # {ts("id_43")}hypercorn
    config = Config()
    config.bind = [f"{host}:{port}"]
    config.accesslog = "-"
    config.errorlog = "-"
    config.loglevel = "INFO"

    # {ts("id_4075")}
    config.keep_alive_timeout = 600  # 10{ts("id_771")}
    config.read_timeout = 600  # 10{ts("id_4076")}

    await serve(app, config)


if __name__ == "__main__":
    asyncio.run(main())
