#!/usr/bin/env python3
"""启动入口：python run.py [端口]"""
import sys
from jobagent.server import main

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8770
    main(port)
