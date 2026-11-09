import io
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table,
    TableStyle, KeepTogether, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from xml.sax.saxutils import escape


def _txt(value, limit=None):
    """Escape user-supplied text so '<', '&' etc. cannot break ReportLab's mini-markup."""
    text = '' if value is None else str(value)
    if limit:
        text = text[:limit]
    return escape(text)

def generate_pdf_report(stats, complaints, staff_list):
    """
    Generates a professional PDF audit report of municipal waste management complaints.
    Returns a BytesIO buffer suitable for Flask send_file.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#198754'),
        fontName='Helvetica-Bold'
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#555555')
    )
    section_style = ParagraphStyle(
        'SectionHeader',
        parent=styles['Heading2'],
        fontSize=13,
        leading=17,
        textColor=colors.HexColor('#0f5132'),
        fontName='Helvetica-Bold',
        spaceBefore=12,
        spaceAfter=6
    )
    cell_style = ParagraphStyle(
        'CellText',
        parent=styles['Normal'],
        fontSize=8,
        leading=10
    )
    cell_bold = ParagraphStyle(
        'CellBold',
        parent=styles['Normal'],
        fontSize=8,
        leading=10,
        fontName='Helvetica-Bold'
    )

    elements = []

    # Header
    elements.append(Paragraph("MUNICIPAL SOLID WASTE MANAGEMENT SYSTEM", title_style))
    elements.append(Paragraph("Official Monthly Sanitation & Grievance Resolution Audit Report", subtitle_style))
    elements.append(Paragraph(f"Generated On: {datetime.now().strftime('%B %d, %Y - %I:%M %p')} | CleanCity Urban Sanitation Authority", subtitle_style))
    elements.append(Spacer(1, 10))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#198754'), spaceAfter=15))

    # Executive Summary Statistics Table
    elements.append(Paragraph("1. Executive Grievance Summary", section_style))
    summary_data = [
        ["Total Complaints Logged", "Cleaned & Resolved", "Active / In Progress", "Pending Triage", "Active Field Personnel"],
        [
            str(stats.get('total', 0)),
            str(stats.get('resolved', 0)),
            str(stats.get('active', 0)),
            str(stats.get('pending', 0)),
            str(len(staff_list))
        ]
    ]

    t_summary = Table(summary_data, colWidths=[108, 108, 108, 108, 108])
    t_summary.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e8f5e9')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#0f5132')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#c3e6cb')),
        ('FONTSIZE', (0, 1), (-1, 1), 12),
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
    ]))
    elements.append(t_summary)
    elements.append(Spacer(1, 15))

    # Field Sanitation Staff Deployment Table
    elements.append(Paragraph("2. Sanitation Staff Deployment Roster", section_style))
    staff_data = [["Emp Code", "Staff Name", "Department", "Assigned Zone", "Status"]]
    for s in staff_list[:10]:
        staff_data.append([
            s.get('employee_code') or 'N/A',
            s.get('name') or 'N/A',
            s.get('department_name') or 'General Sanitation',
            s.get('assigned_zone') or 'Zone A',
            (s.get('status') or 'available').capitalize()
        ])
    if len(staff_data) == 1:
        staff_data.append(["-", "No staff records found", "-", "-", "-"])

    t_staff = Table(staff_data, colWidths=[90, 130, 140, 100, 80])
    t_staff.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f0f4f8')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d0d7de')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(t_staff)
    elements.append(Spacer(1, 15))

    # Complaints Log Table
    elements.append(Paragraph("3. Detailed Complaint Records & Resolution Log", section_style))
    comp_data = [["Code", "Category", "Address / Landmark", "Reported", "Priority", "Status"]]

    for c in complaints[:25]:  # include up to 25 records
        if hasattr(c.get('created_at'), 'strftime'):
            created_str = c['created_at'].strftime('%d/%m/%y')
        elif c.get('created_at'):
            created_str = str(c['created_at'])[:10]
        else:
            created_str = '-'
        comp_data.append([
            Paragraph(_txt(c.get('complaint_code') or '-'), cell_bold),
            Paragraph(_txt(c.get('category_name') or '-'), cell_style),
            Paragraph(_txt(c.get('address') or '-', 40), cell_style),
            Paragraph(_txt(created_str), cell_style),
            Paragraph(_txt(c.get('priority') or 'Medium'), cell_style),
            Paragraph(_txt(c.get('status') or 'Pending'), cell_bold)
        ])

    if len(comp_data) == 1:
        comp_data.append(["-", "No complaint records found in this cycle", "-", "-", "-", "-"])

    t_comp = Table(comp_data, colWidths=[80, 110, 170, 60, 50, 70])
    t_comp.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#198754')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#ced4da')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    elements.append(t_comp)
    elements.append(Spacer(1, 20))

    # Sign-off footer
    elements.append(Paragraph("Certification & Audit Sign-Off", section_style))
    elements.append(Paragraph("This document certifies that the above complaints were recorded and audited in accordance with Municipal Solid Waste Management Directives.", subtitle_style))
    elements.append(Spacer(1, 30))

    sig_data = [
        ["____________________________", "____________________________"],
        ["Chief Sanitation Inspector", "Municipal Commissioner"]
    ]
    t_sig = Table(sig_data, colWidths=[270, 270])
    t_sig.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
    ]))
    elements.append(t_sig)

    doc.build(elements)
    buffer.seek(0)
    return buffer
