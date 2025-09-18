@echo off
title Atualizar Site para o Render

echo ==================================================
echo      ATUALIZANDO O SITE NA INTERNET (RENDER)
echo ==================================================
echo.

REM Navega para a pasta do projeto
cd C:\Users\ImPK\Desktop\loja_pods

echo [1/4] Acessando a pasta do projeto...
echo      - OK
echo.

echo [2/4] Adicionando todas as alteracoes...
git add .
echo      - OK
echo.

echo [3/4] Salvando as alteracoes (commit)...
set /p commit_message=" >> Digite uma breve descricao da atualizacao e pressione Enter: "
git commit -m "%commit_message%"
echo.

echo [4/4] Enviando a atualizacao para o GitHub...
echo      (O Render ira detectar isso e atualizar o site automaticamente)
git push
echo.

echo ==================================================
echo      PROCESSO CONCLUIDO!
echo      Seu site sera atualizado no Render em alguns minutos.
echo ==================================================
echo.
pause