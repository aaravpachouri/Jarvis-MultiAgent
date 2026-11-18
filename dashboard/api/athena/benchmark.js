const { proxyBackendGet } = require("../../lib/backend-proxy");

module.exports = function handler(req, res) {
  return proxyBackendGet(req, res, () => "/api/athena/benchmark");
};
