# Expense Tracker MCP

## How to Run the Server
Please make sure you refer to the [How to build your own local MCP server](https://medium.com/@vinodkrane/how-to-build-your-own-local-mcp-server-e55e248b2fbe) section before proceeding.

1. Open your terminal and navigate to the `expense-tracker-mcp` folder
2. Run the server in dev mode to test it first:
   ```
   uv run mcp dev main.py
   ```
3. Once happy, run it normally:
   ```
   uv run fastmcp run main.py
   ```
4. To connect it to Claude Desktop, run:
   ```
   uv run fastmcp install main.py --name "expense-tracker"
   ```
5. Restart Claude Desktop and click the 🔨 icon to confirm the tools are listed

---

## How to Run the Client
Please make sure you refer to the [How to build your own local MCP client](https://medium.com/@vinodkrane/how-to-build-your-own-local-mcp-client-5460bd415906) section before proceeding.

1. Open your terminal and navigate to the `expense-tracker-client` folder

2. Make sure your `.env` file contains your OpenAI API key:
   ```
   OPENAI_API_KEY=your-actual-api-key-here
   ```
3. Install
   ```
   pip install langchain langchain-openai langchain-mcp-adapters python-dotenv streamlit
   ```

4. Start the Streamlit web app:
   ```
   streamlit run client.py
   ```
5. Your browser will open at `http://localhost:8501`

6. Start chatting — try things like:
   - "Add an expense: £45 on groceries today"
   - "What have I spent this month?"
   - "Summarise my food spending from 2025-01-01 to 2025-03-31"