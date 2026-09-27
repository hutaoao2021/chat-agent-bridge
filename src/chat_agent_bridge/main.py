import argparse
import asyncio
from pathlib import Path
import uvicorn
from .app import Runtime,create_app,create_local_app
from .auth import PairingManager
from .config import Config
from .state import TaskStore

async def serve(config,store,host='127.0.0.1'):
    if host!='127.0.0.1':raise ValueError('only loopback bind supported')
    runtime=Runtime(config,store,store.path.parent/'jobs')
    apps=[(create_app(config,store,runtime=runtime),config.port), (create_local_app(config,store,runtime=runtime),config.extension_port)]
    servers=[uvicorn.Server(uvicorn.Config(app,host=host,port=port,log_level='warning',access_log=False)) for app,port in apps]
    await asyncio.gather(*(s.serve() for s in servers))

def main():
    parser=argparse.ArgumentParser(description='Local MCP execution and paired Chat extension control')
    parser.add_argument('--config',type=Path,default=Path('config.local.toml'))
    parser.add_argument('--data-dir',type=Path,default=Path('.data'))
    parser.add_argument('--pair',action='store_true',help='Print a one-use pairing code and exit (5 minute expiry)')
    args=parser.parse_args();store=TaskStore(args.data_dir/'state.db')
    if args.pair:print(PairingManager(store).issue_code());return
    config=Config.load(args.config)
    print(f'Chat Agent Bridge: MCP http://127.0.0.1:{config.port}/mcp; extension control http://127.0.0.1:{config.extension_port}')
    asyncio.run(serve(config,store))

if __name__=='__main__':main()
