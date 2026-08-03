"""FAQ Service for handling common customer inquiries."""

FAQS = {
    "1": {
        "question": "What are your opening hours?",
        "answer": "We are open Monday to Saturday from 7:30 a.m. to 6:00 p.m. We are open on Sundays from 10:00 a.m. to 4:00 p.m. during the Back-to-School season (August–September). Our opening hours on public holidays may vary. Please call (876) 619-8419 to confirm before visiting."
    },
    "2": {
        "question": "What type of books do you sell?",
        "answer": "We offer a wide range of educational books for students from Kindergarten through Secondary School, including textbooks, workbooks, readers, and other curriculum support materials."
    },
    "3": {
        "question": "Can I preorder a book that is currently out of stock?",
        "answer": "Please call (876) 619-8419 and a representative will inform you if it can be specially ordered and provide you with the necessary details."
    },
    "4": {
        "question": "Do you offer payment plans?",
        "answer": "Yes. To start a payment plan, you'll need to visit the store to make your deposit(s). Your books will be reserved until you complete your payments and will be released to you once the balance has been paid in full."
    },
    "5": {
        "question": "Do you offer booking wrapping on all books?",
        "answer": "Free book wrapping is available only for textbooks purchased from Book Depot. Other books are not eligible for complimentary book wrapping."
    },
    "6": {
        "question": "Do you accept book vouchers?",
        "answer": "Yes, we do accept Book Depot gift vouchers only."
    },
    "7": {
        "question": "Where is the store located?",
        "answer": "Book depot is located at 38 Langston Road, Kingston 3, Vineyard Town."
    },
    "8": {
        "question": "What are your payment methods?",
        "answer": "Our accepted Payment methods are:\n• Cash (In Store)\n• Debit/Credit Cards (In Store)\n• Bank Transfer\n• Zelle Payments"
    },
    "9": {
        "question": "Can I collect my order in store without a receipt?",
        "answer": "Please present your receipt or order confirmation when collecting your order. Orders will only be released upon verification."
    },
    "10": {
        "question": "Do you offer printing?",
        "answer": "Yes, we offer in-store printing services. For more information about our available printing options please contact our office at (876) 619-8419."
    }
}

def get_faq_menu_text() -> str:
    """Generates the interactive menu text."""
    menu = "Welcome to Book Depot! 📚\nPlease reply with a number (1 to 10) to get an instant answer, or send your book list to get a quote:\n\n"
    for key, data in FAQS.items():
        menu += f"{key}. {data['question']}\n"
    return menu

def process_faq(message_text: str, sender_phone: str, whatsapp_client) -> bool:
    """
    Evaluates incoming text. 
    Returns True if handled as an FAQ. 
    Returns False if it should be sent to the book extractor.
    """
    cleaned_text = message_text.strip().lower()

    # 1. Check for menu triggers
    if cleaned_text in ["menu", "faq", "hi", "hello", "help"]:
        menu_text = get_faq_menu_text()
        whatsapp_client.send_text(sender_phone, menu_text)
        return True
        
    # 2. Check for explicit FAQ number selection
    if cleaned_text in FAQS:
        answer = FAQS[cleaned_text]["answer"]
        whatsapp_client.send_text(sender_phone, answer)
        return True
        
    return False