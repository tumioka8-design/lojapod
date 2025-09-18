@echo off
title Servidor Draw Vapes

echo ==================================================
echo      INICIANDO SERVIDOR DA LOJA DRAW VAPES
echo ==================================================
echo.

REM Navega para a pasta do projeto
cd C:\Users\ImPK\Desktop\loja_pods

echo [1/2] Acessando a pasta do projeto...
echo      - OK
echo.

echo [2/2] Iniciando o servidor do site...
echo.
echo    >> O site estara disponivel no seu computador em: http://127.0.0.1:5000
echo    >> Para testar em outros dispositivos (como seu celular), use o IP da sua rede.
echo    >> Pressione CTRL+C nesta janela para desligar o servidor.
echo.

REM Inicia o servidor Flask em modo público na rede local (host=0.0.0.0)
flask run --host=0.0.0.0
