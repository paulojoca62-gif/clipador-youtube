# Segurança

## Chaves
- Ficam em `segredos.dat` (pasta de dados). No Windows: cifradas com DPAPI (`CryptProtectData`, escopo do usuário + entropia própria). Fora do Windows: JSON com permissão 600 (não cifrado — o painel avisa).
- O `config.json` nunca recebe chaves. Instalações 1.x são migradas na primeira abertura: grava o cofre, relê e confere, e só então limpa o JSON. Cofre ilegível não é sobrescrito (vira `segredos.ilegivel`) e o painel pede para reconectar.
- A API do painel devolve só `true/false` para cada chave. O backup de atualização copia o `config.json` sem chaves.
- Logs (arquivo, banco, tela), mensagens de erro da API e avisos do Telegram passam por `seguranca.redigir`: padrões do Google (AIza, ya29, 1//, GOCSPX), Meta (EAA…), TikTok (act./rft.), bot do Telegram, `Bearer`, `code=`, `access_token=`… e os valores exatos salvos pelo usuário.

## Painel local (127.0.0.1:8777)
- Escuta só em 127.0.0.1.
- **Host** precisa ser `127.0.0.1:8777` ou `localhost:8777` (bloqueia DNS rebinding).
- **POST** exige `X-CortesEX: 1` (outro site não consegue enviar sem CORS) e, se houver `Origin`, que seja o próprio painel. PUT/DELETE/PATCH: 405. Corpo máximo 2 MB.
- Cabeçalhos: CSP restritiva, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer` (o código OAuth não vaza), `CORP: same-origin`.
- Vídeos servidos só por id do banco e só se o arquivo estiver dentro da pasta de dados. Nenhum endpoint executa comando ou recebe caminho de arquivo.
- OAuth com `state` aleatório de uso único; TikTok com PKCE.
- Sem autenticação por senha: o modelo de ameaça é “outros sites no mesmo computador”, coberto acima. Outros usuários do mesmo Windows com acesso ao 127.0.0.1 poderiam usar o painel — limitação conhecida.

## IA (prompt injection)
Instruções na mensagem `system`; chat/transcrição/canal na mensagem `user`, entre `<<<DADOS_NAO_CONFIAVEIS>>>`, com marcadores neutralizados e tamanho limitado. A resposta é validada (JSON, campos, tamanhos, hashtags, links removidos, filtro de termos). Nada da resposta é executado.
