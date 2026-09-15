// Replace this with the seller/admin WhatsApp phone number (with country code, no + or spaces)
const ADMIN_WHATSAPP_NUMBER = "919876543210"; 

/**
 * Redirects the user directly to WhatsApp with a pre-filled, formatted message.
 * @param {string} itemType - 'Product', 'Equipment', or 'F2C Produce'
 * @param {string} itemName - Name of the item
 * @param {string|number} price - Price / Rate
 * @param {string} itemDetails - Extra details (e.g., duration, specs, quantity)
 */
function buyViaWhatsApp(itemType, itemName, price, itemDetails = '') {
    // Prompt for quick user details if needed, or pull from input fields
    const userName = prompt("Please enter your name:") || "Customer";
    const userLocation = prompt("Please enter your city/village:") || "Not specified";

    const message = 
`Hello! I am interested in ordering/booking via Yogiraj Agri-Tech.

📋 *Order Details:*
• *Category:* ${itemType}
• *Item:* ${itemName}
• *Price:* ₹${price}
${itemDetails ? `• *Details:* ${itemDetails}\n` : ''}
👤 *Customer Information:*
• *Name:* ${userName}
• *Location:* ${userLocation}

Please share payment and delivery details.`;

    const encodedMessage = encodeURIComponent(message);
    const whatsappUrl = `https://wa.me/${ADMIN_WHATSAPP_NUMBER}?text=${encodedMessage}`;
    
    // Open WhatsApp in a new tab
    window.open(whatsappUrl, '_blank');
}