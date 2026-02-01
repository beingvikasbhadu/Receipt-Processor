from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pytesseract
from PIL import Image
import io
import re
from typing import List, Optional
from datetime import datetime

# Create FastAPI app
app = FastAPI(title="Invoice Processor API - 100% FREE Local OCR")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Define data models
class LineItem(BaseModel):
    description: str
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: float

class InvoiceData(BaseModel):
    vendor_name: str
    date: str
    total_amount: float
    tax_amount: Optional[float] = None
    line_items: List[LineItem]
    confidence_scores: dict

@app.get("/")
async def root():
    return {
        "message": "Invoice Processor API - 100% FREE Local OCR",
        "status": "running",
        "engine": "Tesseract OCR (No API needed, works offline!)"
    }

def extract_invoice_data(text: str) -> dict:
    """Extract invoice data from OCR text using pattern matching"""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    
    vendor_name = ""
    date = ""
    total_amount = 0.0
    tax_amount = None
    line_items = []
    
    # Extract vendor (first meaningful line)
    for line in lines[:10]:
        # Skip short lines and lines that are just numbers/symbols
        if len(line) > 3 and not line.replace('.', '').replace(',', '').replace('$', '').isdigit():
            # Skip common header words
            if not any(skip in line.lower() for skip in ['invoice', 'receipt', 'bill', 'tax', 'total']):
                vendor_name = line
                break
    
    # If still no vendor, try harder
    if not vendor_name and len(lines) > 0:
        vendor_name = lines[0]
    
    # Extract date (multiple formats)
    date_patterns = [
        r'(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',  # MM-DD-YYYY or DD-MM-YYYY
        r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})',    # YYYY-MM-DD
        r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4})',  # DD Month YYYY
    ]
    
    for line in lines:
        for pattern in date_patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                raw_date = match.group(1)
                try:
                    # Try to normalize to YYYY-MM-DD
                    if '/' in raw_date or '-' in raw_date:
                        parts = re.split(r'[-/]', raw_date)
                        if len(parts) == 3:
                            # Detect format
                            if len(parts[0]) == 4:  # YYYY-MM-DD
                                date = f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"
                            elif len(parts[2]) == 4:  # MM-DD-YYYY or DD-MM-YYYY
                                # Assume MM-DD-YYYY (US format)
                                date = f"{parts[2]}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
                            else:  # MM-DD-YY
                                year = f"20{parts[2]}" if int(parts[2]) < 50 else f"19{parts[2]}"
                                date = f"{year}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
                except:
                    date = datetime.now().strftime('%Y-%m-%d')
                break
        if date:
            break
    
    # Extract amounts using various patterns
    for line in lines:
        line_lower = line.lower()
        
        # Find total amount (look for keywords)
        if any(kw in line_lower for kw in ['total', 'amount due', 'balance', 'grand total', 'total amount']):
            # Find all amounts in the line
            amounts = re.findall(r'\$?\s*(\d{1,10}[,.]\d{2})', line)
            if amounts:
                # Take the last (usually the actual total)
                total_amount = float(amounts[-1].replace(',', ''))
        
        # Find tax
        if 'tax' in line_lower and 'total' not in line_lower:
            amounts = re.findall(r'\$?\s*(\d{1,10}[,.]\d{2})', line)
            if amounts:
                tax_amount = float(amounts[-1].replace(',', ''))
        
        # Extract potential line items
        # Skip if it's a total/tax/subtotal line
        if not any(kw in line_lower for kw in ['total', 'tax', 'subtotal', 'discount', 'payment', 'change', 'cash']):
            amounts = re.findall(r'(\d{1,10}[,.]\d{2})', line)
            if amounts and len(line) > 5:
                # Get description (everything before the last amount)
                parts = line.rsplit(amounts[-1], 1)
                if parts[0].strip():
                    desc = parts[0].strip()
                    # Clean up description
                    desc = re.sub(r'^\$?\s*\d+\.?\d*\s*[xX@]?\s*', '', desc)  # Remove leading prices
                    desc = desc.strip('$.,- ')
                    
                    if desc and len(desc) > 2:
                        # Try to extract quantity
                        qty_match = re.search(r'(\d+)\s*[xX@]', line)
                        quantity = float(qty_match.group(1)) if qty_match else None
                        
                        line_items.append({
                            'description': desc[:100],
                            'quantity': quantity,
                            'unit_price': None,
                            'amount': float(amounts[-1].replace(',', ''))
                        })
    
    # Remove duplicate line items
    seen_amounts = set()
    unique_items = []
    for item in line_items:
        if item['amount'] not in seen_amounts or len(unique_items) < 3:
            seen_amounts.add(item['amount'])
            unique_items.append(item)
    
    # Calculate confidence scores based on what we found
    confidence = {
        'vendor_name': 0.8 if vendor_name and len(vendor_name) > 3 else 0.3,
        'date': 0.85 if date else 0.3,
        'total_amount': 0.9 if total_amount > 0 else 0.3,
        'tax_amount': 0.75 if tax_amount else 0.5,
        'line_items': 0.7 if unique_items else 0.3
    }
    
    return {
        'vendor_name': vendor_name or 'Unknown Vendor',
        'date': date or datetime.now().strftime('%Y-%m-%d'),
        'total_amount': total_amount,
        'tax_amount': tax_amount,
        'line_items': unique_items[:15],  # Limit to 15 items
        'confidence_scores': confidence
    }

@app.post("/process-invoice", response_model=InvoiceData)
async def process_invoice(file: UploadFile = File(...)):
    """
    Process invoice using FREE Tesseract OCR (completely local!)
    No API keys needed, works offline!
    """
    try:
        # Read file
        content = await file.read()
        file_extension = file.filename.split('.')[-1].lower()
        
        # Convert PDF to image if needed
        if file_extension == 'pdf':
            import fitz  # PyMuPDF
            pdf = fitz.open(stream=content, filetype="pdf")
            page = pdf[0]
            # Higher DPI for better OCR
            pix = page.get_pixmap(dpi=300)
            img_bytes = pix.tobytes("png")
            image = Image.open(io.BytesIO(img_bytes))
        else:
            image = Image.open(io.BytesIO(content))
        
        # Preprocess image for better OCR
        # Convert to grayscale
        image = image.convert('L')
        
        # Optional: enhance contrast (uncomment if needed)
        # from PIL import ImageEnhance
        # enhancer = ImageEnhance.Contrast(image)
        # image = enhancer.enhance(2)
        
        # Extract text using Tesseract
        text = pytesseract.image_to_string(image, config='--psm 6')
        
        if not text.strip():
            raise HTTPException(
                status_code=400,
                detail="Could not extract text from image. Please ensure the image is clear and contains readable text."
            )
        
        # Parse the extracted text
        invoice_data = extract_invoice_data(text)
        
        return InvoiceData(**invoice_data)
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error processing invoice: {str(e)}"
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)