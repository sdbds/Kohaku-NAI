import asyncio
import os
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import toml
from pydantic import BaseModel

from fastapi import FastAPI, Request, Response
from starlette.middleware.sessions import SessionMiddleware

from utils import generate_novelai_image, free_check, set_token
from hashlib import sha3_256


class GenerateRequest(BaseModel):
    prompt: str
    neg_prompt: str
    seed: int
    scale: float
    width: int
    height: int
    steps: int
    sampler: str
    schedule: str
    smea: bool = False
    dyn: bool = False
    dyn_threshold: bool = False
    chg_rescale: float = 0.0
    img_sub_folder: str = ''


server_config = toml.load("config.toml")["gen_server"]
auth_configs = server_config.get('auth', [])
set_token(server_config['token'])
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=uuid4().hex)

generate_semaphore = asyncio.Semaphore(server_config['max_jobs'])
save_worker = ThreadPoolExecutor(16)


def save_img(sub_folder: str, image: bytes, json: str):
    sub_folder_path = os.path.join(
        server_config['save_path'], sub_folder
    )
    os.makedirs(sub_folder_path, exist_ok=True)
    
    img_hash = sha3_256(image).hexdigest()
    
    with open(os.path.join(sub_folder_path, f'{img_hash}.png'), 'wb') as f:
        f.write(image)
    with open(os.path.join(sub_folder_path, f'{img_hash}.json'), 'w', encoding='utf-8') as f:
        f.write(json)


@app.post("/login")
async def login(password: str, request: Request):
    for auth in auth_configs:
        if password == auth['password']:
            request.session['signed'] = True
            request.session['free_only'] = auth.get('free_only', True)
            return {"status": "login success"}
    else:
        request.session.clear()
        return {"status": "login failed"}


@app.post("/gen")
async def gen(context: GenerateRequest, request: Request):
    signed = request.session.get('signed', False)
    freeonly = request.session.get('free_only', True)
    always_require_auth = server_config['always_require_auth']
    is_free_gen = free_check(context.width, context.height, context.steps)
    if ((not signed and (always_require_auth or not is_free_gen))
        or (freeonly and not is_free_gen)):
        return Response(json.dumps({'status': 'Config not allowed'}), 500)
    
    if request.session.get('signed', False):
        sub_folder = context.img_sub_folder
    else:
        sub_folder = 'free'
    
    async with generate_semaphore:
        img_bytes, json_payload = await generate_novelai_image(
            context.prompt,
            context.neg_prompt,
            context.seed,
            context.scale,
            context.width,
            context.height,
            context.steps,
            context.sampler,
            context.schedule,
            context.smea,
            context.dyn,
            context.dyn_threshold,
            context.chg_rescale,
        )
    
    await asyncio.get_running_loop().run_in_executor(
        save_worker, save_img, sub_folder, img_bytes, json_payload
    )
    
    return Response(img_bytes, media_type="image/png")


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host=server_config['host'], port=server_config['port'])