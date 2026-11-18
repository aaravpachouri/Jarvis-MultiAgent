const { proxyBackendPost } = require("../../lib/backend-proxy");

module.exports = function handler(req, res) {
  return proxyBackendPost(req, res, "/api/quant/refresh");
};

module.exports.config = { api: { bodyParser: false } };
