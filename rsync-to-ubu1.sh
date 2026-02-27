#!/usr/bin/env bash

USER=yossi
SERVER=ubu1.hale-acoustic.ts.net
rsync -avz --exclude .venv --exclude 'data/live/' --exclude '__pycache__' --exclude '.git' --exclude 'reports' ./ $USER@$SERVER:~/projects/swinger/

