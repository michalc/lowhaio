#!/bin/bash

openssl req -new -newkey rsa:2048 -days 3650 -nodes -x509 -subj /CN=selfsigned \
    -keyout private.key \
    -out public.crt
