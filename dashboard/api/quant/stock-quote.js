const { proxyBackendGet, buildQueryPath } = require("../../lib/backend-proxy");

module.exports = function handler(req, res) {
  return proxyBackendGet(req, res, request =>
    buildQueryPath(request, "/api/quant/stock-quote", {
      symbol: 48,
      range: 8,
    })
  );
};
