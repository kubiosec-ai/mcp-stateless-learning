# MCP on the wire: a packet-level walkthrough

Generated from `mcp_stateless.pcap` by `06_pcap_to_markdown.py`. This is the traffic produced by `04_client_demo.py`, so every exchange below corresponds to something in that script.

## What to notice

**1. There is no handshake.** The very first message is `server/discover`, and the client is already sending real requests immediately after. The old `initialize` / `initialized` exchange is gone.

**2. No session header anywhere.** Search the whole trace for `Mcp-Session-Id` and you will not find it. The 2026-07-28 protocol removed it, which is what lets any replica answer any request.

**3. Every request is self-describing.** Each one repeats its `_meta` block with `protocolVersion`, `clientInfo` and `clientCapabilities`. That is the cost of statelessness: a little more on the wire, in exchange for no server-side memory.

**4. Routing headers mirror the body.** `mcp-protocol-version` and `mcp-method` appear as HTTP headers, so a gateway or load balancer can route on them without parsing the JSON-RPC payload.

**5. State is a plain argument.** In the `03` section, `session_id` travels inside `arguments` like any other parameter. That is the whole explicit-handle pattern, visible in the bytes. Watch one session count 1, 2, 3 while a second session independently starts at 1.


> Captured on loopback with a full snaplen (`tcpdump -i lo0 -s 0`). A truncated snaplen silently cuts off the larger `tools/list` responses.


---

## 01 stateless

`127.0.0.1:8001` (stateless_http=True, no session at all)


### 1. server/discover

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8001
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: server/discover
Content-Length: 245

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "server/discover",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 417
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "cacheScope": "private",
    "capabilities": {
      "extensions": {
        "io.modelcontextprotocol/ui": {}
      },
      "logging": {},
      "prompts": {
        "listChanged": false
      },
      "resources": {
        "listChanged": false,
        "subscribe": false
      },
      "tools": {
        "listChanged": false
      }
    },
    "resultType": "complete",
    "supportedVersions": [
      "2026-07-28"
    ],
    "ttlMs": 0,
    "_meta": {
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateless-calculator",
        "version": "4.0.0b1"
      }
    }
  }
}
```


### 2. tools/call (add)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8001
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
Content-Length: 340

{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "add",
    "arguments": {
      "a": 2,
      "b": 3
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 2
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 281
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateless-calculator",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "5.0",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 5.0
    }
  }
}
```


### 3. tools/list

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8001
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/list
Content-Length: 283

{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/list",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug"
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 1364
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "cacheScope": "private",
    "resultType": "complete",
    "tools": [
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Add two numbers. The result depends ONLY on a and b.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "a": {
              "type": "number"
            },
            "b": {
              "type": "number"
            }
          },
          "required": [
            "a",
            "b"
          ]
        },
        "name": "add",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "number"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Multiply two numbers. Same inputs -> always the same output.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "a": {
              "type": "number"
            },
            "b": {
              "type": "number"
            }
          },
          "required": [
            "a",
            "b"
          ]
        },
        "name": "multiply",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "number"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Convert a temperature. No hidden memory involved.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "celsius": {
              "type": "number"
            }
          },
          "required": [
            "celsius"
          ]
        },
        "name": "celsius_to_fahrenheit",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "number"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      }
    ],
    "ttlMs": 0,
    "_meta": {
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateless-calculator",
        "version": "4.0.0b1"
      }
    }
  }
}
```


### 4. tools/call (add)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8001
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
Content-Length: 340

{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "add",
    "arguments": {
      "a": 2,
      "b": 3
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 4
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 281
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateless-calculator",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "5.0",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 5.0
    }
  }
}
```


### 5. tools/call (add)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8001
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
Content-Length: 340

{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "tools/call",
  "params": {
    "name": "add",
    "arguments": {
      "a": 2,
      "b": 3
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 5
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 281
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateless-calculator",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "5.0",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 5.0
    }
  }
}
```


---

## 02 stateful (global)

`127.0.0.1:8002` (one counter shared by every client)


### 1. server/discover

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8002
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: server/discover
Content-Length: 245

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "server/discover",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 414
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "cacheScope": "private",
    "capabilities": {
      "extensions": {
        "io.modelcontextprotocol/ui": {}
      },
      "logging": {},
      "prompts": {
        "listChanged": false
      },
      "resources": {
        "listChanged": false,
        "subscribe": false
      },
      "tools": {
        "listChanged": false
      }
    },
    "resultType": "complete",
    "supportedVersions": [
      "2026-07-28"
    ],
    "ttlMs": 0,
    "_meta": {
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateful-notebook",
        "version": "4.0.0b1"
      }
    }
  }
}
```


### 2. tools/call (increment)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8002
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
Content-Length: 335

{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "increment",
    "arguments": {},
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 2
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 274
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateful-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "1",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 1
    }
  }
}
```


### 3. tools/list

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8002
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/list
Content-Length: 283

{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/list",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug"
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 1676
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "cacheScope": "private",
    "resultType": "complete",
    "tools": [
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Increase a shared counter and return its new value.\n\nCall it repeatedly and the number keeps growing: the server is\nremembering the previous value between calls.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {}
        },
        "name": "increment",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "integer"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Read the current counter without changing it.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {}
        },
        "name": "get_counter",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "integer"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Store a note. It stays in memory for later calls.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "text": {
              "type": "string"
            }
          },
          "required": [
            "text"
          ]
        },
        "name": "add_note",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "string"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Return every note stored so far in this server process.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {}
        },
        "name": "list_notes",
        "outputSchema": {
          "properties": {
            "result": {
              "items": {
                "type": "string"
              },
              "type": "array"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      }
    ],
    "ttlMs": 0,
    "_meta": {
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateful-notebook",
        "version": "4.0.0b1"
      }
    }
  }
}
```


### 4. tools/call (increment)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8002
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
Content-Length: 335

{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "increment",
    "arguments": {},
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 4
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 274
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateful-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "2",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 2
    }
  }
}
```


### 5. tools/call (increment)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8002
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
Content-Length: 335

{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "tools/call",
  "params": {
    "name": "increment",
    "arguments": {},
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 5
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 274
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateful-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "3",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 3
    }
  }
}
```


### 6. tools/call (add_note)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8002
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
Content-Length: 351

{
  "jsonrpc": "2.0",
  "id": 6,
  "method": "tools/call",
  "params": {
    "name": "add_note",
    "arguments": {
      "text": "buy milk"
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 6
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 326
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 6,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateful-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "Stored note #1: 'buy milk'",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": "Stored note #1: 'buy milk'"
    }
  }
}
```


### 7. tools/call (add_note)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8002
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
Content-Length: 353

{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": {
    "name": "add_note",
    "arguments": {
      "text": "call Alice"
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 7
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 330
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateful-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "Stored note #2: 'call Alice'",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": "Stored note #2: 'call Alice'"
    }
  }
}
```


### 8. tools/call (list_notes)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8002
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
Content-Length: 336

{
  "jsonrpc": "2.0",
  "id": 8,
  "method": "tools/call",
  "params": {
    "name": "list_notes",
    "arguments": {},
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 8
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 326
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 8,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateful-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "[\"buy milk\",\"call Alice\"]",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": [
        "buy milk",
        "call Alice"
      ]
    }
  }
}
```


---

## 03 stateful (per-session)

`127.0.0.1:8003` (explicit session-id handles)


### 1. server/discover

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: server/discover
Content-Length: 245

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "server/discover",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 417
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "cacheScope": "private",
    "capabilities": {
      "extensions": {
        "io.modelcontextprotocol/ui": {}
      },
      "logging": {},
      "prompts": {
        "listChanged": false
      },
      "resources": {
        "listChanged": false,
        "subscribe": false
      },
      "tools": {
        "listChanged": false
      }
    },
    "resultType": "complete",
    "supportedVersions": [
      "2026-07-28"
    ],
    "ttlMs": 0,
    "_meta": {
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    }
  }
}
```


### 2. tools/call (create_session)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
Content-Length: 340

{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "create_session",
    "arguments": {},
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 2
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 349
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "32fd291f-e091-4210-a8af-8b029fb6b1e8",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": "32fd291f-e091-4210-a8af-8b029fb6b1e8"
    }
  }
}
```


### 3. tools/list

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/list
Content-Length: 283

{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/list",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug"
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 3313
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "cacheScope": "private",
    "resultType": "complete",
    "tools": [
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Increment a counter that belongs to THIS session only.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "session_id": {
              "type": "string",
              "description": "Session identifier. Use a tool to create a session, then pass the resulting id here to persist state across calls in the same session."
            }
          },
          "required": [
            "session_id"
          ]
        },
        "name": "increment",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "integer"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Store a fact in THIS session's memory.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "session_id": {
              "type": "string",
              "description": "Session identifier. Use a tool to create a session, then pass the resulting id here to persist state across calls in the same session."
            },
            "fact": {
              "type": "string"
            }
          },
          "required": [
            "session_id",
            "fact"
          ]
        },
        "name": "remember",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "string"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Return everything remembered in THIS session.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "session_id": {
              "type": "string",
              "description": "Session identifier. Use a tool to create a session, then pass the resulting id here to persist state across calls in the same session."
            }
          },
          "required": [
            "session_id"
          ]
        },
        "name": "recall",
        "outputSchema": {
          "properties": {
            "result": {
              "items": {
                "type": "string"
              },
              "type": "array"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Create a new session and return its identifier.\n\nMints an unguessable `uuid4`, records an initial session owned by the current\nprincipal, and returns the id as a string. Store it and pass it back as a\n`session_id` argument on later calls to persist state across a session \u2014 only\nan id created this way resolves. State is keyed by the authenticated\nprincipal, so the id organizes sessions within a user; on an unauthenticated\nconnection the id is the only thing standing between callers, which is why it\nis unguessable.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {}
        },
        "name": "create_session",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "string"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "End a session and delete all of its state.\n\nValidates the id like any other resolution (an unknown or foreign id is\nrejected), then deletes the session's key so the id no longer resolves.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "session_id": {
              "type": "string",
              "description": "Session identifier. Use a tool to create a session, then pass the resulting id here to persist state across calls in the same session."
            }
          },
          "required": [
            "session_id"
          ]
        },
        "name": "end_session",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "string"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      }
    ],
    "ttlMs": 0,
    "_meta": {
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    }
  }
}
```


### 4. tools/call (create_session)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
Content-Length: 340

{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "create_session",
    "arguments": {},
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 4
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 349
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "f418df72-2c37-4acb-b06b-d237f6ce10a6",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": "f418df72-2c37-4acb-b06b-d237f6ce10a6"
    }
  }
}
```


### 5. tools/call (increment)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
Content-Length: 386

{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "tools/call",
  "params": {
    "name": "increment",
    "arguments": {
      "session_id": "32fd291f-e091-4210-a8af-8b029fb6b1e8"
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 5
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 277
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "1",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 1
    }
  }
}
```


### 6. tools/call (increment)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
Content-Length: 386

{
  "jsonrpc": "2.0",
  "id": 6,
  "method": "tools/call",
  "params": {
    "name": "increment",
    "arguments": {
      "session_id": "32fd291f-e091-4210-a8af-8b029fb6b1e8"
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 6
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 277
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 6,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "2",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 2
    }
  }
}
```


### 7. tools/call (increment)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
Content-Length: 386

{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": {
    "name": "increment",
    "arguments": {
      "session_id": "32fd291f-e091-4210-a8af-8b029fb6b1e8"
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 7
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 277
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "3",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 3
    }
  }
}
```


### 8. tools/call (increment)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
Content-Length: 386

{
  "jsonrpc": "2.0",
  "id": 8,
  "method": "tools/call",
  "params": {
    "name": "increment",
    "arguments": {
      "session_id": "f418df72-2c37-4acb-b06b-d237f6ce10a6"
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 8
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 277
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 8,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "1",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 1
    }
  }
}
```


### 9. tools/call (increment)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
Content-Length: 386

{
  "jsonrpc": "2.0",
  "id": 9,
  "method": "tools/call",
  "params": {
    "name": "increment",
    "arguments": {
      "session_id": "32fd291f-e091-4210-a8af-8b029fb6b1e8"
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 9
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 277
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 9,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "4",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 4
    }
  }
}
```
