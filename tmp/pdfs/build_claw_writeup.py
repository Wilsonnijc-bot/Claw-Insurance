from pathlib import Path

from PIL import Image
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph


ROOT = Path(r"D:\桌面\Claw-Insurance")
OUT = ROOT / "output" / "pdf" / "Zihan_Yan_HKU.pdf"
TMP = ROOT / "tmp" / "pdfs"
SCREENSHOT = Path(
    r"C:\Users\19124\AppData\Local\Temp\codex-clipboard-a9ceffed-4719-44f5-ba95-b4f3332da4b1.png"
)
CROPPED = TMP / "claw-ui-crop.png"

OUT.parent.mkdir(parents=True, exist_ok=True)
TMP.mkdir(parents=True, exist_ok=True)


PAGE_W, PAGE_H = A4
M = 32
NAVY = HexColor("#0B1F3A")
BLUE = HexColor("#245FD6")
CYAN = HexColor("#DCEBFF")
PALE = HexColor("#F5F8FC")
INK = HexColor("#182433")
MUTED = HexColor("#536273")
LINE = HexColor("#D7E0EB")
GREEN = HexColor("#158267")
SOFT_GREEN = HexColor("#E7F6F1")


def crop_screenshot():
    image = Image.open(SCREENSHOT).convert("RGB")
    w, h = image.size
    target_ratio = 16 / 9
    if w / h > target_ratio:
        new_w = int(h * target_ratio)
        left = max(0, (w - new_w) // 2)
        image = image.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = max(0, (h - new_h) // 2)
        image = image.crop((0, top, w, top + new_h))
    image.save(CROPPED, quality=94)


def draw_paragraph(c, text, x, y_top, width, font_size=11, leading=14, color=INK, bold=False):
    style = ParagraphStyle(
        "body",
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=font_size,
        leading=leading,
        textColor=color,
        alignment=TA_LEFT,
        spaceAfter=0,
        spaceBefore=0,
    )
    p = Paragraph(text, style)
    _, height = p.wrap(width, PAGE_H)
    p.drawOn(c, x, y_top - height)
    return height


def draw_section_title(c, text, x, y, width):
    c.setFillColor(BLUE)
    c.roundRect(x, y - 2, 5, 18, 2, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", 13)
    c.setFillColor(NAVY)
    c.drawString(x + 13, y, text)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.7)
    c.line(x + 13, y - 6, x + width, y - 6)


def draw_card(c, x, y, w, h, fill=white, stroke=LINE, radius=10):
    c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.setLineWidth(0.8)
    c.roundRect(x, y, w, h, radius, fill=1, stroke=1)


def draw_metric(c, x, y, w, value, label, accent=BLUE):
    draw_card(c, x, y, w, 56, fill=PALE, stroke=LINE, radius=9)
    c.setFillColor(accent)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(x + 13, y + 31, value)
    draw_paragraph(c, label, x + 13, y + 25, w - 26, 11, 13, MUTED)


def draw_flow(c, x, y, total_w):
    labels = [
        ("1", "WhatsApp", "message"),
        ("2", "Per-client", "context"),
        ("3", "Local privacy", "filter"),
        ("4", "AI + product", "catalog"),
        ("5", "Human-approved", "send"),
    ]
    gap = 8
    box_w = (total_w - gap * 4) / 5
    box_h = 54
    for i, (number, line1, line2) in enumerate(labels):
        bx = x + i * (box_w + gap)
        fill = SOFT_GREEN if i == 4 else PALE
        stroke = HexColor("#A8DCCB") if i == 4 else LINE
        draw_card(c, bx, y, box_w, box_h, fill=fill, stroke=stroke, radius=8)
        c.setFillColor(GREEN if i == 4 else BLUE)
        c.circle(bx + 15, y + 39, 8, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", 11)
        num_w = stringWidth(number, "Helvetica-Bold", 11)
        c.drawString(bx + 15 - num_w / 2, y + 35.5, number)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(bx + 9, y + 20, line1)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 11)
        c.drawString(bx + 9, y + 7, line2)
        if i < 4:
            ax = bx + box_w + 1
            ay = y + box_h / 2
            c.setStrokeColor(BLUE)
            c.setLineWidth(1.2)
            c.line(ax, ay, ax + gap - 3, ay)
            c.line(ax + gap - 6, ay + 2.5, ax + gap - 3, ay)
            c.line(ax + gap - 6, ay - 2.5, ax + gap - 3, ay)


def build_pdf():
    crop_screenshot()
    c = canvas.Canvas(str(OUT), pagesize=A4, pageCompression=1)
    c.setTitle("Claw Insurance - Polymer Tech Expo 2026 Project Write-up")
    c.setAuthor("Zihan Yan")
    c.setSubject("One-page AI project write-up")

    # Header
    c.setFillColor(BLUE)
    c.roundRect(M, PAGE_H - 56, 151, 22, 11, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(M + 12, PAGE_H - 49, "POLYMER TECH EXPO")

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 25)
    c.drawString(M, PAGE_H - 91, "CLAW INSURANCE")
    c.setFillColor(BLUE)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(M, PAGE_H - 111, "Privacy-first AI copilot for insurance advisors on WhatsApp")
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 11)
    c.drawRightString(PAGE_W - M, PAGE_H - 51, "Zihan Yan | The University of Hong Kong")
    c.drawRightString(PAGE_W - M, PAGE_H - 70, "Project write-up | 2026")

    # Impact strip
    metric_y = PAGE_H - 184
    available = PAGE_W - 2 * M
    metric_gap = 9
    metric_w = (available - metric_gap * 2) / 3
    draw_metric(c, M, metric_y, metric_w, "20+", "paying users", BLUE)
    draw_metric(c, M + metric_w + metric_gap, metric_y, metric_w, "2+ hours", "saved per user per day", GREEN)
    draw_metric(c, M + (metric_w + metric_gap) * 2, metric_y, metric_w, "Real revenue", "feedback-driven MVP", BLUE)

    # Workflow
    workflow_title_y = metric_y - 27
    draw_section_title(c, "END-TO-END WORKFLOW", M, workflow_title_y, available)
    draw_flow(c, M, workflow_title_y - 72, available)

    # Main columns
    left_x = M
    gap = 16
    right_w = 192
    left_w = available - right_w - gap
    right_x = left_x + left_w + gap
    top_y = workflow_title_y - 95

    # Left column: problem, solution, AI
    draw_section_title(c, "PROBLEM STATEMENT", left_x, top_y, left_w)
    problem = (
        "Insurance advisors repeatedly answer product and policy questions in WhatsApp, "
        "while each reply depends on long client histories and sensitive personal data. "
        "Generic chatbots are fast but can leak private information, mix client context, "
        "or recommend products without reliable grounding."
    )
    h = draw_paragraph(c, problem, left_x, top_y - 16, left_w, 11, 14)

    sol_y = top_y - 28 - h
    draw_section_title(c, "SOLUTION OVERVIEW", left_x, sol_y, left_w)
    solution = (
        "Claw Insurance is a Docker-deployed local workspace that synchronizes WhatsApp "
        "conversations, keeps every client in an isolated session, and generates an editable "
        "reply draft. A human advisor reviews and sends the final message. The same workspace "
        "also supports catalog-grounded product matching and offline meeting-note transcription."
    )
    h2 = draw_paragraph(c, solution, left_x, sol_y - 16, left_w, 11, 14)

    ai_y = sol_y - 28 - h2
    draw_section_title(c, "USE OF AI", left_x, ai_y, left_w)
    ai_items = [
        ("Context:", "The prompt combines the selected client's isolated history, local memory, and the latest incoming message."),
        ("Grounding:", "An insurance-product advisor skill retrieves structured catalog evidence before drafting a recommendation."),
        ("Privacy:", "A local deterministic gateway masks names, phone numbers, chat IDs, policy numbers, and addresses before cloud inference; residual risk fails closed."),
        ("Control:", "The model produces a draft only. The same draft ID and phone number must be validated before a human-approved WhatsApp send."),
    ]
    cursor = ai_y - 16
    for label, body in ai_items:
        line = f"<b>{label}</b> {body}"
        used = draw_paragraph(c, line, left_x, cursor, left_w, 11, 14)
        cursor -= used + 4

    # Right column: screenshot
    draw_card(c, right_x, top_y - 144, right_w, 160, fill=white, stroke=LINE, radius=10)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(right_x + 10, top_y + 1, "PRODUCT WORKSPACE")
    c.drawImage(
        ImageReader(str(CROPPED)),
        right_x + 8,
        top_y - 108,
        width=right_w - 16,
        height=99,
        preserveAspectRatio=True,
        anchor="c",
        mask="auto",
    )
    draw_paragraph(
        c,
        "One interface for clients, privacy status, AI drafts, approval, and meeting notes.",
        right_x + 10,
        top_y - 116,
        right_w - 20,
        11,
        13,
        MUTED,
    )

    impact_y = top_y - 176
    draw_section_title(c, "IMPACT & VALUE", right_x, impact_y, right_w)
    impact = (
        "The MVP has 20+ paying users, including advisors from Prudential and AIA, who "
        "report saving 2+ hours per day on repeated questions and follow-up drafts. "
        "Human approval and local privacy controls make the speed gain usable in a "
        "high-trust workflow."
    )
    impact_h = draw_paragraph(c, impact, right_x, impact_y - 16, right_w, 11, 14)

    reflection_y = impact_y - 29 - impact_h
    draw_section_title(c, "REFLECTIONS", right_x, reflection_y, right_w)
    reflections = [
        "Main challenges: WhatsApp connectivity, phone normalization, and the Docker-to-host browser boundary.",
        "Key lesson: per-client isolation, fail-closed privacy, and human approval make failures recoverable.",
        "Next: delivery receipts, NER-assisted redaction, automated draft evaluation, and deeper CRM/catalog integrations.",
    ]
    cursor = reflection_y - 17
    for item in reflections:
        c.setFillColor(BLUE)
        c.circle(right_x + 4, cursor - 4, 2.2, fill=1, stroke=0)
        used = draw_paragraph(c, item, right_x + 12, cursor, right_w - 12, 11, 14)
        cursor -= used + 5

    # Footer technology line
    footer_y = 28
    c.setStrokeColor(LINE)
    c.setLineWidth(0.8)
    c.line(M, footer_y + 17, PAGE_W - M, footer_y + 17)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 11)
    c.drawString(M, footer_y, "Stack: Python | React + TypeScript | NanoBot | LiteLLM | WhatsApp Bridge | Supabase | Docker")

    c.showPage()
    c.save()
    print(OUT)


if __name__ == "__main__":
    build_pdf()
