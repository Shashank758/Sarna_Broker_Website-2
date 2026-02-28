# Saarna Broker Platform

A comprehensive commodity trading and brokerage platform connecting agricultural stakeholders (Farmers, Millers, and Buyers) in a unified digital marketplace.

## 🌟 Overview

The Saarna Broker Platform is a full-stack web application designed to streamline the trading of agricultural commodities like Wheat, Rice, Mustard, Maize, and Paddy. It provides role-specific dashboards, live market rates, Quality Control (QC) tracking, and administrative oversight to ensure secure and transparent transactions.

## 👥 User Roles

1. **Farmer**:
   - Post available crops/commodities for sale.
   - Track active listings and connect with local Millers.
2. **Miller (Industrial Partner)**:
   - Procure raw materials from Farmers.
   - Post processed commodities (e.g., Rice, Mustard, Wheat) to the Live Market.
   - Set delivery durations, prices, and available quantities.
   - Conduct strict Quality Control (QC) checks on loaded vehicles (recording metrics like Moisture, Broken percentage, Karda, etc.).
   - Manage dispatch, truck loading, and final Hisab (settlements).
3. **Buyer (Commercial Outlet)**:
   - Browse the Live Market to view available commodities posted by Millers.
   - Book stock directly from the platform.
   - Track booking statuses, view uploaded invoices, and manage pending payments.
4. **Admin**:
   - Verify and approve user registrations.
   - Audit compliance documents (GST filings, Mandi licenses).
   - Monitor global orders, revenue, and platform statistics.
   - Oversee the entire seller network and resolve disputes.

## 🛠️ Tech Stack

- **Backend**: Python, Flask
- **Database**: PostgreSQL (with `psycopg2` adapter)
- **Frontend**: HTML5, CSS3, JavaScript, Bootstrap 5
- **Authentication**: JWT-based session management / Flask-Session
- **File Handling**: Secure document uploads for compliance and invoicing via `werkzeug`

## 🚀 Key Features

- **Live Market Feed**: Real-time visibility into available commodities, pricing, and seller details.
- **Dynamic Quality Control (QC)**: Crop-specific quality assessment forms (e.g., 'Broken' metrics for Rice, 'Karda' metrics for Mustard).
- **Compliance Verification**: Mandatory uploads of GST and Mandi License documents for corporate entities (Millers/Buyers).
- **Order Lifecycle Management**: End-to-end tracking from booking → loading → QC verification → invoicing → payment.
- **Automated Calculations**: Automatic deduction computations based on QC metrics (moisture loss, weight reduction thresholds).

## ⚙️ Installation & Setup

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd Sarna_Broker_Website-2
   ```

2. **Create a Virtual Environment**:
   ```bash
   python -m venv venv
   # Windows:
   venv\Scripts\activate
   # Linux/Mac:
   source venv/bin/activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Variables Config**:
   Create a `.env` file in the root directory and add your application secrets:
   ```env
   FLASK_APP=app.py
   FLASK_ENV=development
   FLASK_DEBUG=1
   SECRET_KEY=your_secret_key_here
   DATABASE_URL=postgresql://user:password@localhost:5432/saarna_db
   ```

5. **Database Initialization**:
   The application uses a custom migration setup. Ensure your PostgreSQL instance is running and the database matches your connection URI. When `app.py` is executed, it establishes the schema.

6. **Run the Application**:
   ```bash
   flask run
   # Or directly:
   python app.py
   ```

## 📁 Project Structure

- `/templates`: HTML views (Dashboards, Admin panels, Auth forms).
- `/static`: CSS (`style.css`), JavaScript, and uploaded user compliance files/invoices.
- `app.py`: Primary application entry point containing routing and SQL database interactions.
- `requirements.txt`: Python package dependencies.

## 🔐 Security Considerations

- All uploaded files uses `secure_filename()` validation.
- Compliance forms require strict document verification by the Super Admin before traders are fully visible on the network.
- Custom route protectors ensure role-based access control (RBAC).

## 📝 License

*(Insert your specific project license here, e.g., MIT, Proprietary, etc.)*
