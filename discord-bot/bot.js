const axios = require("axios");
require("dotenv").config();

const { Client, GatewayIntentBits } = require("discord.js");

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent
  ]
});

// ========== Kiểm tra kết nối webhook n8n ==========
async function checkWebhookConnection() {
  const webhookUrl = process.env.N8N_WEBHOOK;
  console.log("=================================");
  console.log("🔍 Kiểm tra kết nối webhook n8n...");
  console.log("URL:", webhookUrl);

  try {
    // Gửi POST test để kiểm tra webhook
    const response = await axios.post(webhookUrl, {
      test: true,
      timestamp: new Date().toISOString()
    }, { timeout: 10000 });
    console.log(`✅ Webhook n8n hoạt động (status: ${response.status})`);
  } catch (err) {
    if (err.response) {
      // Server trả về response (vd: 404, 500)
      console.error(`❌ Webhook n8n trả về lỗi: HTTP ${err.response.status}`);
      if (err.response.status === 404) {
        console.error("   → Endpoint không tồn tại. Kiểm tra lại URL hoặc ngrok tunnel.");
      }
    } else if (err.code === "ECONNREFUSED") {
      console.error("❌ Không thể kết nối đến server n8n (ECONNREFUSED)");
      console.error("   → n8n server có thể chưa chạy hoặc ngrok tunnel đã hết hạn.");
    } else if (err.code === "ENOTFOUND") {
      console.error("❌ Không tìm thấy host (ENOTFOUND)");
      console.error("   → Kiểm tra lại domain ngrok có đúng không.");
    } else if (err.code === "ETIMEDOUT" || err.code === "ECONNABORTED") {
      console.error("❌ Kết nối timeout (ETIMEDOUT)");
      console.error("   → Server không phản hồi trong 10 giây.");
    } else {
      console.error("❌ Lỗi kết nối webhook:", err.message);
    }
  }
  console.log("=================================");
}
// ==================================================

client.once("ready", async () => {
  console.log(`✅ Bot online: ${client.user.tag}`);
  await checkWebhookConnection();
});

client.on("messageCreate", async (message) => {

  if (message.author.bot) return;

  console.log("=================================");
  console.log("MESSAGE RECEIVED");
  console.log("Guild:", message.guild?.name);
  console.log("Channel:", message.channel?.name);
  console.log("Author:", message.author.username);
  console.log("Content:", message.content);
  console.log("=================================");

  try {

    await axios.post(process.env.N8N_WEBHOOK, {
      id: message.id,
      guild_id: message.guild?.id,
      guild_name: message.guild?.name,
      channel_id: message.channel?.id,
      channel_name: message.channel?.name,
      author: message.author.username,
      content: message.content,
      timestamp: new Date().toISOString()
    });

    console.log("✅ Sent to n8n");

  } catch (err) {

    console.error("❌ Webhook Error");
    console.error(err.message);

  }

});

client.login(process.env.DISCORD_TOKEN);