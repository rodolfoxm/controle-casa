# Segurança

## Como reportar uma vulnerabilidade

Encontrou uma falha de segurança? **Não abra uma issue pública.** Use o botão
**Report a vulnerability**, na aba **Security** deste repositório, para mandar um aviso privado.

Conte o que encontrou, como reproduzir e qual versão do Controle Casa você usa (aparece em ⚙ Ajustes).
A resposta vem o quanto antes, e a correção sai numa nova versão.

## Onde ficam os seus dados

- Tudo fica **só no seu computador**, em `%APPDATA%\ControleCasa\config.json`. O projeto não tem
  servidor próprio nem coleta dados.
- O **Access Secret da Tuya** e a **senha da Positivo** são gravados **protegidos pelo Windows (DPAPI)**:
  só abrem na mesma conta de usuário, no mesmo computador.
- O programa conversa apenas com a nuvem da Tuya e, se você ligar a conta, com a da Positivo.
- A API local só aceita pedidos do próprio PC (`127.0.0.1`) com o cabeçalho do app. Sites abertos no
  navegador não conseguem controlar seus aparelhos.
- O relatório de Diagnóstico não inclui chaves nem senhas, mas inclui nomes e IDs dos seus aparelhos.
  Revise antes de compartilhar.
