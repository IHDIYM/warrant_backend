from flask import Flask, request, jsonify
from flask_cors import CORS
from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from google.generativeai import GenerativeModel, configure
import logging
from pymongo import MongoClient
from datetime import datetime
from bson import ObjectId, json_util
import uuid
import os

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={
    r"/api/*": {
        "origins": [
            "http://localhost:5173",
            "http://localhost:5174",
            "https://waaranty-assistant.netlify.app"  # <-- your Netlify site
        ],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True
    }
})

# Create FAISS directory if it doesn't exist
FAISS_PATH = "faiss_index"
if not os.path.exists(FAISS_PATH):
    os.makedirs(FAISS_PATH)
    logger.info(f"Created FAISS directory at {FAISS_PATH}")

# MongoDB setup
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb+srv://rtkvma:4uVbFCTl1dG9Y23u@ai-cluster.tsnsuuu.mongodb.net/?retryWrites=true&w=majority&appName=ai-cluster")

# Initialize MongoDB connection with error handling
client = None
db = None
users_collection = None
technicians_collection = None
chats_collection = None
purchases_collection = None

try:
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    # Test the connection
    client.admin.command('ping')
    db = client.warranty_assistant
    users_collection = db.users
    technicians_collection = db.technicians
    chats_collection = db.chats
    purchases_collection = db.purchases
    logger.info("Successfully connected to MongoDB")
    
    # Add a default technician account if it doesn't exist
    default_technician = {
        'email': 'tech@warranty.com',
        'whatsapp': '1234567890',
        'name': 'Default Technician',
        'role': 'technician'
    }

    if not technicians_collection.find_one({'email': default_technician['email']}):
        technicians_collection.insert_one(default_technician)
        logger.info("Created default technician account")
        
except Exception as e:
    logger.warning(f"Failed to connect to MongoDB: {str(e)}")
    logger.warning("Running in local mode without database functionality")
    # Create mock collections for local development
    class MockCollection:
        def find_one(self, *args, **kwargs):
            return None
        def insert_one(self, *args, **kwargs):
            return type('obj', (object,), {'inserted_id': 'mock_id'})()
        def update_one(self, *args, **kwargs):
            return type('obj', (object,), {'modified_count': 1})()
        def find(self, *args, **kwargs):
            return []
        def update_many(self, *args, **kwargs):
            return type('obj', (object,), {'modified_count': 0})()
    
    users_collection = MockCollection()
    technicians_collection = MockCollection()
    chats_collection = MockCollection()
    purchases_collection = MockCollection()

# Configure Google Gemini API
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
configure(api_key=GOOGLE_API_KEY)

def get_or_create_chat_session(user_id, username):
    # Find the most recent active session for this user
    chat_session = chats_collection.find_one({
        "user_id": user_id,
        "active": True
    })
    
    if not chat_session:
        # Create a new chat session
        chat_session = {
            "user_id": user_id,
            "username": username,
            "active": True,
            "messages": [],
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "login_time": datetime.utcnow()
        }
        result = chats_collection.insert_one(chat_session)
        chat_session['_id'] = result.inserted_id
    else:
        # Update login time for existing session
        chats_collection.update_one(
            {"_id": chat_session["_id"]},
            {"$set": {"login_time": datetime.utcnow()}}
        )
    
    return chat_session

def update_chat_session(chat_id, message, response):
    chat_messages = [
        {
            "role": "user",
            "content": message,
            "timestamp": datetime.utcnow()
        },
        {
            "role": "assistant",
            "content": response,
            "timestamp": datetime.utcnow()
        }
    ]
    
    # Convert string ID to ObjectId if it's not already an ObjectId
    if isinstance(chat_id, str) and chat_id != 'mock_id':
        try:
            chat_id = ObjectId(chat_id)
        except:
            pass  # Keep as string if conversion fails
    
    chats_collection.update_one(
        {"_id": chat_id},
        {
            "$push": {"messages": {"$each": chat_messages}},
            "$set": {"updated_at": datetime.utcnow()}
        }
    )

def get_chat_history(chat_id, limit=5):
    # Convert string ID to ObjectId if it's not already an ObjectId
    if isinstance(chat_id, str) and chat_id != 'mock_id':
        try:
            chat_id = ObjectId(chat_id)
        except:
            pass  # Keep as string if conversion fails
    
    chat = chats_collection.find_one({"_id": chat_id})
    if not chat:
        return []
    messages = chat.get("messages", [])
    return messages[-limit:] if limit else messages

def get_response(context, question, chat_history=None):
    try:
        model = GenerativeModel("gemini-1.5-flash")

        system_prompt = """## System Prompt

You are a product warranty assistant for consumer electronics and appliances. Your job is to help customers understand product models, warranty coverage, and whether they can file a complaint or claim under the product warranty based on their specific issue.

### Product Information Structure
Each product has the following information:
1. Brand and Category
2. Available Models with PIDs (optional for warranty claims)
3. Warranty Coverage
4. Common Issues Covered
5. Common Exclusions

### Communication Guidelines
- Be clear, professional, and helpful
- When asked about models, list all available models with their PIDs
- When asked about warranty, explain coverage and conditions
- When asked about issues, explain if they're covered and why
- Make intelligent decisions based on available information
- Only ask for more information if absolutely necessary (like purchase date or brand)
- If the issue isn't covered, explain politely and suggest checking with the brand
- Use the vector database to find relevant information and make informed decisions

### Response Format
- For model listings: Start with "Here are the available models:"
- For warranty info: Start with "Warranty Coverage:"
- For issue coverage: Start with "✅ Yes" or "❌ No"
- For unclear cases: Start with "ℹ️ More info needed"
- Keep your answer under 500 characters if possible

### Example Responses:
1. "What are the available models and warranty coverage?"
   "Here's a comprehensive overview by brand and category:

   Voltas:
   - ACs: 1.0-2.0 Ton models with 1 year standard + 10 year compressor warranty
   - Air Coolers: Desert & Personal series with 1 year warranty
   - Refrigerators: Commercial & Voltas Beko with 1 year product + 10 year compressor
   - Visi Coolers & Water Dispensers: 1 year standard warranty

   Blue Star:
   - ACs: 1.0-2.0 Ton with 1 year standard + 5 year compressor warranty
   - All models available in Standard, Pro, and Elite variants

   Panasonic:
   - ACs: 1.0-2.0 Ton with 1 year standard + 5 year compressor warranty
   - Microwaves: 1 year standard + 5 year magnetron warranty
   - Washing Machines: 2 year standard + 10 year motor warranty
   - TVs: 1 year standard + 3 year panel warranty"

2. "My AC compressor stopped working after 3 years"
   "✅ Yes. If it's an inverter AC with a 10-year compressor warranty and registered on time, you're eligible."

3. "My microwave plate broke due to a fall"
   "❌ No. Physical damage like a fall is excluded from standard warranty coverage."

4. "My BlueStar AC is on extended warranty and its motor is not working it been 1 year since purchase"
   "✅ Yes. Since your Blue Star AC is on extended warranty and the motor issue occurred within the warranty period, this is covered. The extended warranty includes motor coverage. Please contact the nearest service center with your purchase receipt."

5. "Voltas AC not cooling after 2 years"
   "❌ No. The standard warranty covers manufacturing defects for 1 year. Since it's been 2 years, this issue is not covered under standard warranty. However, if you have extended warranty or if it's an inverter model with a 10-year compressor warranty, please provide your purchase date to verify coverage."

6. "Panasonic washing machine making noise"
   "ℹ️ More info needed. Please provide your purchase date to check warranty coverage. The standard warranty covers manufacturing defects for 2 years, and motor issues for 10 years on select models."

7. "Voltas Beko refrigerator not cooling"
   "ℹ️ More info needed. Please provide your purchase date. The standard warranty covers 1 year on product and 10 years on compressor (parts only)."

8. "Panasonic TV screen flickering"
   "ℹ️ More info needed. Please provide your purchase date. The standard warranty covers 1 year comprehensive, and panel issues for 3 years on select models."

9. "Blue Star AC water leakage"
   "ℹ️ More info needed. Please provide your purchase date. Water leakage can be covered if it's due to manufacturing defects and within the 1-year standard warranty period."

10. "Voltas air cooler not working"
    "ℹ️ More info needed. Please provide your purchase date. The standard warranty covers manufacturing defects for 1 year, and regular cleaning is required to maintain warranty coverage."

### Product Database Context:
{context}

{chat_context}
### Customer Question: 
{question}
"""

        # Include chat history in the prompt if available
        chat_context = ""
        if chat_history:
            chat_context = "\n### Previous Conversation:\n"
            for msg in chat_history:
                role = "Customer" if msg["role"] == "user" else "Assistant"
                chat_context += f"{role}: {msg['content']}\n"

        prompt = f"""{system_prompt}

### Warranty Database Context:
{context}

{chat_context}
### Customer Question: 
{question}
"""

        response = model.generate_content(prompt)
        response_text = response.text.strip()
        if len(response_text) > 500:
            response_text = response_text[:497] + "..."
        return response_text
    except Exception as e:
        logger.error(f"Error in get_response: {str(e)}")
        raise e

def query_database(query_text, chat_id=None):
    try:
        embedding_function = GoogleGenerativeAIEmbeddings(
            model="models/embedding-001",
            google_api_key=GOOGLE_API_KEY
        )

        try:
            # Load FAISS index without the unsupported parameter
            db = FAISS.load_local(
                FAISS_PATH, 
                embedding_function
            )
        except Exception as e:
            logger.error(f"Error loading FAISS index: {str(e)}")
            # If index loading fails, create a new one with sample data
            sample_data = [
                # Air Coolers - Voltas
                "Voltas Air Cooler Models: Desert Air Cooler - DZ [PID: VOL-DZ-2024], DZ Pro [PID: VOL-DZP-2024], DZ Elite [PID: VOL-DZE-2024]",
                "Voltas Air Cooler Models: Personal Air Cooler - PZ [PID: VOL-PZ-2024], PZ Pro [PID: VOL-PZP-2024], PZ Elite [PID: VOL-PZE-2024]",
                "Voltas Air Cooler warranty covers manufacturing defects for 1 year",
                "Voltas Air Cooler physical damage is not covered under warranty",
                "Voltas Air Cooler requires regular cleaning to maintain warranty",
                
                # Air Conditioners - Voltas
                "Voltas AC Models: 1.5 Ton - 183V DZT [PID: VOL-AC-183V-2024], 185V DZT [PID: VOL-AC-185V-2024], 185V DZR [PID: VOL-AC-185VZ-2024], 185V DZR Pro [PID: VOL-AC-185VZP-2024], 185V DZR Elite [PID: VOL-AC-185VZE-2024]",
                "Voltas AC Models: 1.0 Ton - 123V DZT [PID: VOL-AC-123V-2024], 125V DZT [PID: VOL-AC-125V-2024], 125V DZR [PID: VOL-AC-125VZ-2024], 125V DZR Pro [PID: VOL-AC-125VZP-2024], 125V DZR Elite [PID: VOL-AC-125VZE-2024]",
                "Voltas AC Models: 2.0 Ton - 243V DZT [PID: VOL-AC-243V-2024], 245V DZT [PID: VOL-AC-245V-2024], 245V DZR [PID: VOL-AC-245VZ-2024], 245V DZR Pro [PID: VOL-AC-245VZP-2024], 245V DZR Elite [PID: VOL-AC-245VZE-2024]",
                "Voltas AC warranty covers manufacturing defects for 1 year",
                "Voltas AC inverter compressor has lifetime warranty (10 years)",
                "Voltas AC physical damage is not covered under standard warranty",
                "Voltas AC regular maintenance is required every 3 months",
                
                # Air Conditioners - Blue Star
                "Blue Star AC Models: 1.5 Ton - 5W18 [PID: BLS-AC-5W18-2024], 5W18 Pro [PID: BLS-AC-5W18P-2024], 5W18 Elite [PID: BLS-AC-5W18E-2024]",
                "Blue Star AC Models: 1.0 Ton - 3W12 [PID: BLS-AC-3W12-2024], 3W12 Pro [PID: BLS-AC-3W12P-2024], 3W12 Elite [PID: BLS-AC-3W12E-2024]",
                "Blue Star AC Models: 2.0 Ton - 7W24 [PID: BLS-AC-7W24-2024], 7W24 Pro [PID: BLS-AC-7W24P-2024], 7W24 Elite [PID: BLS-AC-7W24E-2024]",
                "Blue Star AC warranty covers manufacturing defects for 1 year",
                "Blue Star AC inverter compressor has 5-year warranty",
                "Blue Star AC PCB and coils are covered under warranty",
                
                # Air Conditioners - Panasonic
                "Panasonic AC Models: 1.5 Ton - CS/CU-SU18 [PID: PAN-AC-SU18-2024], CS/CU-SU18 Pro [PID: PAN-AC-SU18P-2024]",
                "Panasonic AC Models: 1.0 Ton - CS/CU-SU12 [PID: PAN-AC-SU12-2024], CS/CU-SU12 Pro [PID: PAN-AC-SU12P-2024]",
                "Panasonic AC Models: 2.0 Ton - CS/CU-SU24 [PID: PAN-AC-SU24-2024], CS/CU-SU24 Pro [PID: PAN-AC-SU24P-2024]",
                "Panasonic AC warranty covers manufacturing defects for 1 year",
                "Panasonic AC inverter compressor has 5-year warranty",
                "Panasonic AC PCB, Condenser, Motor, Eco Casing covered for 5 years",
                
                # Commercial Refrigerators - Voltas
                "Voltas Commercial Refrigerator Models: Chest Freezer - CF [PID: VOL-CF-2024], CF Pro [PID: VOL-CFP-2024]",
                "Voltas Commercial Refrigerator Models: Glass Top - GT [PID: VOL-GT-2024], GT Pro [PID: VOL-GTP-2024]",
                "Voltas Commercial Refrigerator Models: Convertible - CV [PID: VOL-CV-2024], CV Pro [PID: VOL-CVP-2024]",
                "Voltas Commercial Refrigerator warranty covers 4 years comprehensive",
                "Voltas Commercial Refrigerator gas and labor not covered after 1 year",
                
                # Visi Coolers & Water Dispensers - Voltas
                "Voltas Visi Cooler Models: VC [PID: VOL-VC-2024], VC Pro [PID: VOL-VCP-2024], VC Elite [PID: VOL-VCE-2024]",
                "Voltas Water Dispenser Models: WD [PID: VOL-WD-2024], WD Pro [PID: VOL-WDP-2024], WD Elite [PID: VOL-WDE-2024]",
                "Voltas Visi Cooler warranty covers 1 year standard",
                "Voltas Water Dispenser warranty covers 1 year standard",
                "Voltas Visi Cooler and Water Dispenser extended warranty available with registration",
                
                # Microwave Ovens - Panasonic
                "Panasonic Microwave Models: Solo - NN-ST25 [PID: PAN-MW-ST25-2024], NN-ST27 [PID: PAN-MW-ST27-2024]",
                "Panasonic Microwave Models: Convection - NN-CT25 [PID: PAN-MW-CT25-2024], NN-CT27 [PID: PAN-MW-CT27-2024]",
                "Panasonic Microwave Models: Grill - NN-GT25 [PID: PAN-MW-GT25-2024], NN-GT27 [PID: PAN-MW-GT27-2024]",
                "Panasonic Microwave warranty covers 1 year comprehensive",
                "Panasonic Microwave magnetron has 5-year warranty on select models",
                
                # Washing Machines - Panasonic
                "Panasonic Washing Machine Models: Front Load - NA-F70 [PID: PAN-WM-F70-2024], NA-F80 [PID: PAN-WM-F80-2024]",
                "Panasonic Washing Machine Models: Top Load - NA-F65 [PID: PAN-WM-F65-2024], NA-F75 [PID: PAN-WM-F75-2024]",
                "Panasonic Washing Machine Models: Semi-Auto - NA-W65 [PID: PAN-WM-W65-2024], NA-W75 [PID: PAN-WM-W75-2024]",
                "Panasonic Washing Machine warranty covers 2 years comprehensive",
                "Panasonic Washing Machine motor has 10-year warranty on select models",
                
                # Refrigerators - Voltas Beko
                "Voltas Beko Refrigerator Models: Single Door - SD [PID: VB-FR-SD-2024], SD Pro [PID: VB-FR-SDP-2024]",
                "Voltas Beko Refrigerator Models: Double Door - DD [PID: VB-FR-DD-2024], DD Pro [PID: VB-FR-DDP-2024]",
                "Voltas Beko Refrigerator Models: Side by Side - SBS [PID: VB-FR-SBS-2024], SBS Pro [PID: VB-FR-SBSP-2024]",
                "Voltas Beko Refrigerator warranty covers 1 year on product",
                "Voltas Beko Refrigerator compressor has 10-year warranty",
                
                # Televisions - Panasonic
                "Panasonic TV Models: LED - TH-43 [PID: PAN-TV-43-2024], TH-50 [PID: PAN-TV-50-2024], TH-55 [PID: PAN-TV-55-2024]",
                "Panasonic TV Models: Smart TV - TH-43S [PID: PAN-TV-43S-2024], TH-50S [PID: PAN-TV-50S-2024], TH-55S [PID: PAN-TV-55S-2024]",
                "Panasonic TV Models: OLED - TH-48O [PID: PAN-TV-48O-2024], TH-55O [PID: PAN-TV-55O-2024], TH-65O [PID: PAN-TV-65O-2024]",
                "Panasonic TV warranty covers 1 year comprehensive",
                "Panasonic TV panel has 3-year warranty on select models",
                
                # General warranty info
                "Warranty claims must be filed within 30 days of issue discovery",
                "Service center visits are required for warranty claims",
                "Original purchase receipt is required for warranty claims",
                "Unauthorized repairs void the warranty",
                "Natural disasters are not covered under warranty"
            ]
            db = FAISS.from_texts(sample_data, embedding_function)
            db.save_local(FAISS_PATH)

        results = db.similarity_search(query_text, k=3)
        if not results:
            return "❌ No matching warranty information found."

        context_text = "\n\n".join([doc.page_content for doc in results])
        
        # Get chat history if chat_id is provided
        chat_history = get_chat_history(chat_id) if chat_id else None
        
        return get_response(context_text, query_text, chat_history)
    except Exception as e:
        logger.error(f"Error in query_database: {str(e)}")
        raise

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({'status': 'ok', 'message': 'Warranty AI API is running'}), 200

@app.route('/api/query', methods=['POST'])
def handle_query():
    try:
        data = request.json
        query_text = data.get('prompt', '').strip()
        user_id = data.get('userId')
        username = data.get('username')
        
        if not query_text:
            return jsonify({'error': 'No prompt provided'}), 400
        if not user_id:
            return jsonify({'error': 'No user ID provided'}), 400
        if not username:
            return jsonify({'error': 'No username provided'}), 400

        logger.info(f"Received query from user {username} (ID: {user_id}): {query_text}")

        # Get or create chat session with username
        chat_session = get_or_create_chat_session(user_id, username)
        chat_id = str(chat_session['_id'])

        # Handle basic greetings and small talk
        greeting_inputs = ["hi", "hello", "hey", "good morning", "good afternoon", "good evening"]
        smalltalk_inputs = ["how are you", "how's it going", "who are you", "what is your name", "are you there", "are you listening"]

        lowered = query_text.lower()
        
        # Check for direct product queries first
        product_keywords = ["model", "models", "available", "list", "show", "warranty", "coverage", "panasonic", "voltas", "blue star", "washing", "ac", "refrigerator", "microwave", "tv", "cooler"]
        if any(keyword in lowered for keyword in product_keywords):
            response = query_database(query_text, chat_id)
            update_chat_session(chat_id, query_text, response)
            return jsonify({"response": response})
        
        if any(phrase in lowered for phrase in greeting_inputs):
            response = f"""👋 Hi {username}! I can help you check your product warranty. Please select a product category:

1️⃣ Air Coolers
2️⃣ Air Conditioners
3️⃣ Commercial Refrigerators
4️⃣ Visi Coolers & Water Dispensers
5️⃣ Microwave Ovens
6️⃣ Washing Machines
7️⃣ Refrigerators
8️⃣ Televisions

Please reply with the number of your product category (e.g., "1" for Air Coolers)."""
            update_chat_session(chat_id, query_text, response)
            return jsonify({"response": response})

        if any(phrase in lowered for phrase in smalltalk_inputs):
            if "who are you" in lowered or "what is your name" in lowered:
                response = f"🤖 I'm your Warranty Assistant, {username}! I'm here to help you check product warranty eligibility. Please select a product category by typing 1-8."
            else:
                response = f"😊 I'm just a bot, but I'm ready to help you, {username}! Please select a product category by typing 1-8."
            update_chat_session(chat_id, query_text, response)
            return jsonify({"response": response})

        # Handle category selection only if it's a single number
        if query_text.strip() in ['1', '2', '3', '4', '5', '6', '7', '8']:
            categories = {
                '1': 'Air Cooler',
                '2': 'Air Conditioner',
                '3': 'Commercial Refrigerator',
                '4': 'Visi Cooler or Water Dispenser',
                '5': 'Microwave Oven',
                '6': 'Washing Machine',
                '7': 'Refrigerator',
                '8': 'Television'
            }
            selected_category = categories[query_text.strip()]
            response = f"""Great! You've selected {selected_category}. Please provide the following details:

1. Brand name
2. Year of purchase
3. Issue you're facing

For example: "Samsung, Model, 2022, Not cooling properly" """
            update_chat_session(chat_id, query_text, response)
            return jsonify({"response": response})

        # For all other queries, use the vector database
        response = query_database(query_text, chat_id)
        update_chat_session(chat_id, query_text, response)
        
        logger.info(f"Generated response for {username}")
        return jsonify({'response': response})
    except Exception as e:
        logger.error(f"Error in handle_query: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/users', methods=['POST'])
def create_user():
    try:
        data = request.json
        name = data.get('name')
        email = data.get('email')
        whatsapp = data.get('whatsapp')
        
        if not all([name, email, whatsapp]):
            return jsonify({'error': 'Missing required fields'}), 400
            
        # Check if user already exists
        existing_user = users_collection.find_one({'email': email})
        if existing_user:
            return jsonify({'error': 'User already exists'}), 409
            
        # Create new user
        user = {
            '_id': str(uuid.uuid4()),
            'name': name,
            'email': email,
            'whatsapp': whatsapp,
            'created_at': datetime.utcnow()
        }
        
        users_collection.insert_one(user)
        return jsonify(user), 201
        
    except Exception as e:
        logger.error(f"Error in create_user: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/auth', methods=['POST'])
def authenticate():
    try:
        data = request.json
        email = data.get('email')
        whatsapp = data.get('whatsapp')
        is_technician = data.get('isTechnician', False)
        
        if not all([email, whatsapp]):
            return jsonify({'error': 'Missing required fields'}), 400
            
        # Find user based on role
        if is_technician:
            user = technicians_collection.find_one({
                'email': email,
                'whatsapp': whatsapp
            })
        else:
            user = users_collection.find_one({
                'email': email,
                'whatsapp': whatsapp
            })
        
        if not user:
            return jsonify({'error': 'Invalid credentials'}), 401
            
        # Convert ObjectId to string and add role
        if hasattr(user['_id'], '__str__'):
            user['_id'] = str(user['_id'])
        user['role'] = 'technician' if is_technician else 'user'
        
        return jsonify(user), 200
        
    except Exception as e:
        logger.error(f"Error in authenticate: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat-history/<user_id>', methods=['GET'])
def get_user_chat_history(user_id):
    try:
        # Get all chat sessions for the user, sorted by most recent first
        chat_sessions = list(chats_collection.find(
            {"user_id": user_id}
        ).sort("created_at", -1))
        
        # Convert ObjectId to string for JSON serialization
        for session in chat_sessions:
            if hasattr(session['_id'], '__str__'):
                session['_id'] = str(session['_id'])
            # Convert datetime objects to ISO format strings
            if hasattr(session['created_at'], 'isoformat'):
                session['created_at'] = session['created_at'].isoformat()
            if hasattr(session['updated_at'], 'isoformat'):
                session['updated_at'] = session['updated_at'].isoformat()
            if hasattr(session['login_time'], 'isoformat'):
                session['login_time'] = session['login_time'].isoformat()
            for message in session['messages']:
                if hasattr(message['timestamp'], 'isoformat'):
                    message['timestamp'] = message['timestamp'].isoformat()
        
        return jsonify({
            'sessions': chat_sessions,
            'total_sessions': len(chat_sessions)
        })
    except Exception as e:
        logger.error(f"Error getting chat history: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat-history/<user_id>/<session_id>', methods=['GET'])
def get_chat_session(user_id, session_id):
    try:
        # Convert string ID to ObjectId if it's not already an ObjectId
        if isinstance(session_id, str) and session_id != 'mock_id':
            try:
                session_id = ObjectId(session_id)
            except:
                pass  # Keep as string if conversion fails
        
        # Get specific chat session
        chat_session = chats_collection.find_one({
            "_id": session_id,
            "user_id": user_id
        })
        
        if not chat_session:
            return jsonify({'error': 'Chat session not found'}), 404
            
        # Convert ObjectId to string for JSON serialization
        if hasattr(chat_session['_id'], '__str__'):
            chat_session['_id'] = str(chat_session['_id'])
        # Convert datetime objects to ISO format strings
        if hasattr(chat_session['created_at'], 'isoformat'):
            chat_session['created_at'] = chat_session['created_at'].isoformat()
        if hasattr(chat_session['updated_at'], 'isoformat'):
            chat_session['updated_at'] = chat_session['updated_at'].isoformat()
        if hasattr(chat_session['login_time'], 'isoformat'):
            chat_session['login_time'] = chat_session['login_time'].isoformat()
        for message in chat_session['messages']:
            if hasattr(message['timestamp'], 'isoformat'):
                message['timestamp'] = message['timestamp'].isoformat()
        
        return jsonify(chat_session)
    except Exception as e:
        logger.error(f"Error getting chat session: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/purchases/<user_id>', methods=['GET'])
def get_user_purchases(user_id):
    try:
        # Get all purchases for the user, sorted by purchase date (most recent first)
        purchases = list(purchases_collection.find(
            {"userId": user_id}
        ).sort("purchaseDate", -1))
        
        # If no purchases found, return empty list
        if not purchases:
            return jsonify({
                'purchases': [],
                'total_purchases': 0
            })
        
        # Convert ObjectId to string for JSON serialization
        for purchase in purchases:
            if hasattr(purchase['_id'], '__str__'):
                purchase['_id'] = str(purchase['_id'])
        
        return jsonify({
            'purchases': purchases,
            'total_purchases': len(purchases)
        })
    except Exception as e:
        logger.error(f"Error getting purchases: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
