"""Attachment and model capability helpers."""
import os

_PREFIX = {
    "OpenAI": "openai",
    "Google": "gemini",
    "Anthropic": "anthropic",
    "OpenCode Go": "openai",
    "Custom/Local": "openai",
}

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
PDF_SUFFIXES = (".pdf",)


def cache_control_supported(model: str) -> bool:
    """
    Whether a `cache_control` block on a message reaches the wire as a cache
    breakpoint rather than as a key the provider was never asked about.

    ADR-0004's own spike is the evidence, and it is narrower than that
    ADR's own claim: LiteLLM carries `cache_control` through to
    `AnthropicConfig.transform_request` for a model routed through Anthropic's
    own API, which is `model.startswith("anthropic/")` and nothing else here —
    not the `openai/` gateway, not a bare `api_base` model LiteLLM has never
    heard of, and not `ClaudeCodeClient`'s subscription CLI, which never spoke
    to this transform in the first place. Guessing yes for one of those risks
    nothing on the wire; guessing no costs one field nobody reads.
    """
    return bool(model) and model.startswith("anthropic/")


def litellm_model_name(provider: str, model_id: str) -> str:
    """'Anthropic', 'claude-3-7-sonnet-20250219' -> 'anthropic/claude-3-7-...'."""
    prefix = _PREFIX.get(provider)
    if prefix is None:
        return model_id
    if model_id.startswith(prefix + "/"):
        return model_id
    return prefix + "/" + model_id


def accepts_images(model: str):
    """
    Whether *model* takes images: True, False, or None when LiteLLM has never
    heard of it — which a Custom/Local model is, and which is not a no.
    """
    import litellm

    try:
        litellm.get_model_info(model)
    except Exception:
        return None
    return bool(litellm.supports_vision(model))


def accepts_pdf(model: str):
    """
    The exact twin of `accepts_images`, over the same table.

    Anthropic, OpenAI, Vertex AI, Bedrock and Mistral take a PDF on
    `/chat/completions`; None for a model LiteLLM has never heard of, because a
    Custom/Local model is not a no.

    Read off `get_model_info` rather than through `litellm.supports_pdf_input`:
    the flag is in the table in every version, the top-level predicate is not —
    the LiteLLM this addon ships against (1.95.0) has the one and not the other.
    """
    import litellm

    try:
        info = litellm.get_model_info(model)
    except Exception:
        return None
    return bool(info.get("supports_pdf_input"))


def unusable_attachments(paths, model: str) -> list:
    """
    One sentence per attachment this run would not be able to use, before it
    starts. Empty means send it.

    The provider's own complaint arrives after the phase has already been
    called, which is too late to be told the picture was ignored (FCC-14).

    A PDF is not an image, so a run carrying nothing but PDFs is not refused
    for a model that cannot see — the PDF check is the one that decides it.
    """
    reasons = []
    pdfs, pictures = [], []
    for path in paths:
        name = os.path.basename(path)
        suffix = os.path.splitext(path)[1].lower()
        if not os.path.isfile(path):
            reasons.append(
                "%s is not there any more, so there would be nothing to send." % name)
        elif suffix in PDF_SUFFIXES:
            pdfs.append(name)
        elif suffix in IMAGE_SUFFIXES:
            pictures.append(name)
        else:
            reasons.append(
                "%s is not something the provider will take. Attach a PNG, JPEG, GIF, "
                "WebP or PDF instead." % name)

    nothing_but_pdfs = bool(pdfs) and not pictures
    if paths and not nothing_but_pdfs and accepts_images(model) is False:
        reasons.append(
            "%s cannot accept images. Send the Intent without the attachment, or pick a "
            "model with image support." % model)
    if pdfs and accepts_pdf(model) is False:
        reasons.append(
            "%s cannot read a PDF, so %s would not reach it. Attach an image of the "
            "drawing instead, or pick a model that reads PDFs."
            % (model, ", ".join(pdfs)))
    return reasons
