// The loopback half of the OAuth flow: a local server that receives Google's redirect.
import http from "node:http";
import type { AddressInfo } from "node:net";

export interface CallbackServer {
  redirectUri: string;
  /** The address the server is actually bound to. */
  host: string;
  /** Resolves with the authorization code; rejects on error, state mismatch or timeout. */
  waitForCode(expectedState: string, timeoutMs: number): Promise<string>;
  close(): void;
}

/** Listen on 127.0.0.1 only (never on the network) at a random free port. */
export async function startCallbackServer(): Promise<CallbackServer> {
  const server = http.createServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { address, port } = server.address() as AddressInfo; // listen() on a TCP port always yields AddressInfo
  const redirectUri = `http://127.0.0.1:${port}`;

  return {
    redirectUri,
    host: address,
    waitForCode: (expectedState, timeoutMs) => waitForCode(server, redirectUri, expectedState, timeoutMs),
    close: () => {
      server.closeAllConnections();
      server.close();
    },
  };
}

function waitForCode(server: http.Server, base: string, expectedState: string, timeoutMs: number): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Timed out waiting for Google sign-in.")), timeoutMs);

    server.on("request", (request, response) => {
      const params = new URL(request.url ?? "/", base).searchParams;
      if (!params.has("code") && !params.has("error")) {
        response.writeHead(404).end(); // e.g. the browser asking for /favicon.ico
        return;
      }
      // Plain text responses, so nothing from the query string is ever rendered as HTML.
      const reply = (status: number, body: string): void => {
        clearTimeout(timer);
        response.writeHead(status, { "content-type": "text/plain; charset=utf-8" }).end(body);
      };
      const fail = (body: string, reason: string): void => {
        reply(400, body);
        reject(new Error(reason));
      };

      const error = params.get("error");
      const code = params.get("code");
      if (error) return fail("Sign-in was cancelled or failed.", `Google returned an error: ${error}`);
      if (params.get("state") !== expectedState) return fail("Unexpected sign-in response.", "State mismatch; ignoring this callback.");
      if (!code) return fail("Missing authorization code.", "Callback had no authorization code.");
      reply(200, "Inbox connected. You can close this tab.");
      resolve(code);
    });
  });
}
