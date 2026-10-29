import logging

import uvicorn

from config import load_settings
from webapp import create_app


def main():
    settings = load_settings()
    if settings.host not in ('127.0.0.1', 'localhost', '::1') and not settings.deployment_mode:
        raise RuntimeError('Esta version solo se inicia en localhost, el despliegue se preparara despues')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, access_log=False, proxy_headers=settings.deployment_mode, forwarded_allow_ips='*' if settings.deployment_mode else '127.0.0.1')


if __name__ == '__main__':
    main()
