# Viewing this repository in GitDiagram

[GitDiagram](https://gitdiagram.com) turns a GitHub repository into an interactive architecture diagram. This
repository is private, so GitDiagram needs permission to read it. There are two ways; both were checked against
GitDiagram's source (`ahmedkhaleel2004/gitdiagram`, MIT) on 2026-09-29.

**What leaves our hands either way:** GitDiagram reads the file tree and the README through the GitHub API and
sends them to a language model (OpenAI by default, or OpenRouter). It does not read file contents beyond those.
The repository holds no patient data, customer data or secrets (the `no-secrets` acceptance check runs gitleaks
over the whole history), so this is a decision about sharing our own design, not about regulated data.

## Option 1: the hosted site with a read-only token (recommended, about two minutes)

1. On GitHub, create a fine-grained personal access token: Settings, Developer settings, Fine-grained tokens,
   Generate new token. Resource owner: NodarisAI. Repository access: only `NodarisAI/Coding-Agent_Harness`.
   Permissions: Contents, read-only (Metadata, read-only is added automatically). Expiry: 7 days.
2. Open https://gitdiagram.com, click **Private Repos** in the header and paste the token.
3. Open https://gitdiagram.com/NodarisAI/Coding-Agent_Harness.
4. When you are done, revoke the token on GitHub.

The token is kept by GitDiagram's server so it can call GitHub for you; the short expiry and single read-only
repository limit what it can do.

## Option 2: run GitDiagram on this Mac

Self-hosting keeps the token on this machine, but the file tree still goes to OpenAI or OpenRouter, and the app
needs three cloud services of its own:

- Node.js 22.12 or later and Bun 1.3.14
- Cloudflare R2 (account id, access key, secret key, a public and a private bucket) and a cache key secret
- Upstash Redis (REST address and token)
- An OpenAI or OpenRouter API key
- A GitHub token for the private repository, which can be passed at start-up as `GITHUB_PAT="$(gh auth token)"`

```bash
git clone https://github.com/ahmedkhaleel2004/gitdiagram.git ~/src/gitdiagram
cd ~/src/gitdiagram && bun install && cp .env.example .env
```

Fill in `.env` yourself (the agent never handles keys), then start it with `GITHUB_PAT="$(gh auth token)" bun run dev`
and open http://localhost:3000/NodarisAI/Coding-Agent_Harness.

## Without GitDiagram

The harness builds its own maps of this repository with `nodaris-harness graph`: `graphify-out/graph.html` is a
browsable graph and `graphify-out/GRAPH_REPORT.md` a written summary, and nothing leaves the machine.
