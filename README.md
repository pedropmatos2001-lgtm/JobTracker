# Tracker de vagas

Corre sozinho todas as manhãs (dias úteis), lê fontes estruturadas, filtra pelo teu perfil e manda uma notificação push para o iPhone (e, se quiseres, email). Não depende do Claude nem de ti teres o portátil ligado.

## Como funciona (6 passos)

1. **Recolher**: cada fonte tem um adaptador (Workable, Greenhouse, Lever, SmartRecruiters, Adzuna, Remotive, páginas sem API). Uma fonte avariada nunca derruba as outras.
2. **Deduplicar**: `state/state.json` guarda o que já viu. Só vagas novas continuam.
3. **Filtro barato**: título (sem estágios, júnior, engenharia de software), local (Portugal ou remoto elegível), idade (≤ 14 dias).
4. **Detalhe**: para as que sobram, vai buscar a descrição completa. Se a fonte responder 404, a vaga está fechada e é descartada.
5. **Pontuação** (em `config.yaml`): palavras-chave do teu CV, nível acima (senior/manager/lead), anos de experiência pedidos (exclui < 3 e > 6; avisa a partir de 5), línguas que não falas (neerlandês, alemão, francês), salário publicado abaixo de 35.000 €.
6. **Avisar**: máximo 5 vagas por mensagem, com os riscos a confirmar. Se uma fonte falhar 3 dias seguidos, avisa. À segunda-feira, se não houver nada, manda "estou vivo" com o estado das fontes.

## Instalar (15 minutos)

1. Cria um repositório **privado** no GitHub e carrega esta pasta toda (inclui `.github/`).
2. **iPhone**: instala a app **ntfy** (App Store) e subscreve um tópico com nome impossível de adivinhar, por exemplo `vagas-pedro-7f3k9x2q`. Quem souber o nome lê as mensagens; trata-o como uma palavra-passe.
3. No repositório: *Settings → Secrets and variables → Actions → New repository secret*:
   - `NTFY_TOPIC` = o nome do tópico (obrigatório para o push).
   - `ADZUNA_APP_ID` e `ADZUNA_APP_KEY` = chaves gratuitas em developer.adzuna.com (sem elas a fonte Adzuna fica "ignorada").
   - Email, se quiseres: `SMTP_HOST`, `SMTP_PORT` (465), `SMTP_USER`, `SMTP_PASS` (no Gmail, uma palavra-passe de aplicação), `EMAIL_TO`.
4. Separador *Actions* → "Vagas diárias" → **Run workflow**. Vê os registos: cada fonte diz `ok, N vagas` ou o erro.
5. Opcional, página no ecrã principal do iPhone: *Settings → Pages* → ramo `main`, pasta `/docs`. Isto exige repositório público (ou plano pago), e a lista de critérios em `config.yaml` ficaria visível; se não quiseres isso, ignora este passo e usa só o ntfy.

## Acrescentar empresas

Procura a página de carreiras da empresa e vê o endereço:

| Endereço contém | Tipo | Campo |
|---|---|---|
| `apply.workable.com/NOME/` | `workable` | `account: NOME` |
| `boards.greenhouse.io/NOME` ou `job-boards.eu.greenhouse.io/NOME` | `greenhouse` | `token: NOME` |
| `jobs.lever.co/NOME` | `lever` | `company: NOME` |
| `jobs.smartrecruiters.com/NOME` | `smartrecruiters` | `company: NOME` |
| `xxx.wdN.myworkdayjobs.com/SITE` | `workday` | `tenant: xxx`, `wd: wdN`, `site: SITE` |
| `xxx.jobs.personio.de` | `personio` | `account: xxx` |
| `xxx.teamtailor.com` (ou domínio próprio com /jobs.rss) | `teamtailor` | `host: xxx.teamtailor.com` |
| `xxx.recruitee.com` | `recruitee` | `company: xxx` |
| outra (SuccessFactors, página própria) | `page_watch` | `url` + `link_regex` |

Em `page_watch`, cada ligação nova é aberta e lida pela marcação JSON-LD `JobPosting` (a mesma que o Google usa): se existir, o tracker fica com data, local e salário (mensal conta x14) e descarta a vaga se `validThrough` já passou. Se a página não tiver essa marcação, a vaga passa como "por confirmar".

## Limites (a ler)

- **Os formatos das APIs foram escritos pela documentação pública e não foram chamados ao vivo** (o ambiente onde isto foi construído não tinha acesso a essas páginas). Os 76 testes usam respostas simuladas. A primeira execução real é o teste verdadeiro; se uma fonte vier com erro, o registo diz qual e porquê.
- **LinkedIn não está incluído**: exige sessão e proíbe recolha automática. Usa os alertas do próprio LinkedIn como complemento.
- Muitas empresas grandes (Big 4, Siemens, EDP) usam sistemas sem API pública. Para essas, `page_watch` funciona só se a página não depender de JavaScript; caso contrário avisa que devolveu 0 ligações.
- O Adzuna só devolve um excerto da descrição, por isso anos e línguas podem não ser detetados; essas vagas levam o aviso "descrição parcial".
- Quase nenhuma vaga publica salário. Vagas sem salário passam com o aviso "sem salário indicado".

## Exemplos de fontes novas (copia para `sources:` do `config.yaml` só depois de confirmares o endereço)

```yaml
  - {type: workday, name: "Empresa X", tenant: xxx, wd: wd3, site: SITE, search: "sustainability"}
  - {type: personio, name: "Empresa Y", account: yyy}
  - {type: teamtailor, name: "Empresa Z", host: zzz.teamtailor.com}
  - {type: recruitee, name: "Empresa W", company: www}
```

O Workday usa um endereço não documentado e pode mudar sem aviso.

## Testar localmente

```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
python -m jobtracker --offline tests/fixtures/sample_jobs.json --dry-run
```

## Empresas do Norte já ligadas (verificado em nov. 2026)

| Empresa | Como é vigiada |
|---|---|
| EDP (Porto, Lisboa) | `page_watch` na pesquisa do portal jobs.edp.com |
| Porto Ambiente | `page_watch` na página /recrutamento (vagas em PDF) |
| LIPOR | `page_watch` na página de procedimentos concursais |
| Quadrante, Indaqua, Águas do Douro e Paiva, grupo AdP | sem lista pública; só candidatura espontânea / LinkedIn. Cobertas pela tarefa diária Claude |
| Galp, BP, REN, Efacec, Águas do Norte, Floene | páginas de carreiras ainda por confirmar |

Para acrescentar mais, copia um bloco `page_watch` do `config.yaml`.

## Juiz de IA, kits de candidatura e radar semanal (v3)

**Juiz de IA.** Cada vaga que passa nos filtros é comparada com o teu perfil (segredo `CANDIDATE_PROFILE`; `candidate.example.md` é só o modelo) por um modelo da Anthropic. Devolve um *fit* de 0 a 100, a decisão (candidatar / talvez / ignorar), os riscos concretos e se **vale mudar de emprego**. Abaixo de `min_fit` (55) não recebes aviso. Se a API falhar ou não houver chave, o tracker cai para as palavras-chave e continua a funcionar.

**Repositório público.** Se o repositório for público, NUNCA ponhas dados pessoais em ficheiros. Guarda o teu perfil no segredo `CANDIDATE_PROFILE` (e, se quiseres, o salário mínimo em `MIN_SALARY_EUR`). Com `public_repo: true` (por defeito) o tracker não grava vagas, notas do juiz nem kits no git; os kits seguem só por email (precisa de `SMTP_*`) e o radar semanal fica desligado.

Para ligar: cria uma chave em console.anthropic.com e guarda-a no GitHub em *Settings → Secrets and variables → Actions* com o nome `ANTHROPIC_API_KEY`. O custo é limitado por `max_calls` (40 avaliações por dia, normalmente poucos cêntimos). **Edita o `candidate.md`** com o teu CV real: quanto mais concreto, melhor o juiz.

**Kits de candidatura.** Para as vagas com fit ≥ 70 (máx. 3 por dia) o tracker grava um rascunho de carta, 5 pontos do CV a destacar, perguntas prováveis de entrevista e o que confirmar. O prompt proíbe inventar experiência. Tu revês e submetes.

**Radar semanal.** À sexta-feira recebes um resumo: quantas vagas passaram, que empresas estão a contratar e se alguma justificou mudar de emprego.

### Candidaturas automáticas: porque não
Tecnicamente seria possível, mas não o recomendo nem o ligo:
- O LinkedIn proíbe automação de candidaturas e restringe contas por isso. Perder a conta no meio de uma procura é um risco real.
- Uma candidatura enviada sem ti lê-se como lixo ao nível de recrutador, e para consultoria e setor público a qualidade pesa mais do que a quantidade.
- Cada formulário tem perguntas sobre salário, disponibilidade e línguas que devem ser respondidas por ti.

O meio-termo que o tracker faz: tudo o que antecede o clique (encontrar, avaliar, redigir carta e CV adaptados). O clique final é teu e demora 2 minutos.
