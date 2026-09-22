# Ethical Use & Data Residency

DataForge was developed for **educational and research purposes** —
specifically to explore how publicly available web content can be
transformed into fine-tuning datasets for LLMs.

**Please use this tool responsibly:**

- **Respect `robots.txt` and Terms of Service.** DataForge honours
  `robots.txt` directives by default. Before scraping any site, verify you
  have permission to do so under that site's terms.
- **Do not collect personal data.** Avoid targeting pages that contain
  personally identifiable information (PII), protected health information,
  or other sensitive data. You are responsible for ensuring your dataset
  complies with applicable privacy laws (GDPR, CCPA, etc.).
- **Data residency.** When using cloud-hosted LLM providers (OpenAI,
  Anthropic, Google, Groq, Together AI, etc.), scraped content is
  transmitted to those providers for generation and scoring. If your source
  material is subject to data residency requirements, use a **local model
  via Ollama** so data never leaves your machine.
- **Respect copyright.** Publicly accessible does not mean freely reusable.
  Ensure your intended use of the collected content is consistent with the
  source site's copyright and licensing terms.
- **Rate limiting.** The default rate limit is 2 requests/second per domain.
  Do not lower this value to the point where it disrupts the availability of
  target sites.

This tool is provided as-is for learning purposes. The author assumes no
liability for misuse.
