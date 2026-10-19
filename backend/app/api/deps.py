from fastapi import Request


def get_providers(request: Request) -> dict:
    return request.app.state.providers


def get_gateway(request: Request):
    return request.app.state.gateway


def get_llm(request: Request):
    return request.app.state.llm


def get_whatsapp(request: Request):
    return request.app.state.whatsapp
