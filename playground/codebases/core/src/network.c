/*
 * network.c - Network I/O implementation
 * 
 * Handles I/O multiplexing, packet routing, and protocol
 * dispatch for the emulated network subsystem.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include "../include/network.h"

/*
 * Create network context
 */
NetworkContext *network_create(void)
{
    NetworkContext *ctx = malloc(sizeof(NetworkContext));
    if (!ctx) {
        return NULL;
    }
    
    ctx->connections = NULL;
    ctx->connection_count = 0;
    ctx->packet_queue = NULL;
    ctx->packet_handler = NULL;
    ctx->handler_user_data = NULL;
    
    return ctx;
}

/*
 * Destroy network context
 */
void network_destroy(NetworkContext *ctx)
{
    if (!ctx) {
        return;
    }
    
    /* Free connections */
    if (ctx->connections) {
        free(ctx->connections);
    }
    
    /* Free packet queue */
    NetworkPacket *pkt = ctx->packet_queue;
    while (pkt) {
        NetworkPacket *next = pkt->next;
        if (pkt->payload) {
            free(pkt->payload);
        }
        free(pkt);
        pkt = next;
    }
    
    free(ctx);
}

/*
 * Initialize network context
 */
int network_init(NetworkContext *ctx)
{
    if (!ctx) {
        return ERR_INVALID_PARAM;
    }
    
    /* Allocate connection pool */
    ctx->connections = calloc(MAX_CONNECTIONS, sizeof(NetworkConnection));
    if (!ctx->connections) {
        return ERR_OUT_OF_MEMORY;
    }
    
    return ERR_SUCCESS;
}

/*
 * Connect to remote host
 */
int network_connect(NetworkContext *ctx, const char *host, uint16_t port)
{
    if (!ctx || !host) {
        return ERR_INVALID_PARAM;
    }
    
    /* Find free connection slot */
    for (size_t i = 0; i < MAX_CONNECTIONS; i++) {
        if (!ctx->connections[i].is_connected) {
            ctx->connections[i].socket_fd = socket(AF_INET, SOCK_STREAM, 0);
            if (ctx->connections[i].socket_fd < 0) {
                return ERR_IO_ERROR;
            }
            
            safe_strcpy(ctx->connections[i].remote_addr, 
                       sizeof(ctx->connections[i].remote_addr), host);
            ctx->connections[i].remote_port = port;
            ctx->connections[i].is_connected = true;
            ctx->connection_count++;
            
            return (int)i;  /* Return connection ID */
        }
    }
    
    return ERR_OUT_OF_MEMORY;
}

/*
 * Listen on port
 */
int network_listen(NetworkContext *ctx, uint16_t port)
{
    if (!ctx) {
        return ERR_INVALID_PARAM;
    }
    
    int sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) {
        return ERR_IO_ERROR;
    }
    
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(port);
    
    if (bind(sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(sock);
        return ERR_IO_ERROR;
    }
    
    listen(sock, 10);
    return sock;
}

/*
 * Accept connection
 */
int network_accept(NetworkContext *ctx)
{
    (void)ctx;
    /* Stub implementation */
    return 0;
}

/*
 * Close connection
 */
void network_close_connection(NetworkContext *ctx, int conn_id)
{
    if (!ctx || conn_id < 0 || (size_t)conn_id >= MAX_CONNECTIONS) {
        return;
    }
    
    if (ctx->connections[conn_id].is_connected) {
        close(ctx->connections[conn_id].socket_fd);
        ctx->connections[conn_id].is_connected = false;
        ctx->connection_count--;
    }
}

/*
 * Receive bytes from a connected peer into the caller-supplied buffer.
 * Returns the number of bytes read, or a negative error code.
 */
int network_recv_data(NetworkContext *ctx, int conn_id, void *buf, size_t size)
{
    if (!ctx || !buf || conn_id < 0 || (size_t)conn_id >= MAX_CONNECTIONS) {
        return ERR_INVALID_PARAM;
    }
    
    if (!ctx->connections[conn_id].is_connected) {
        return ERR_INVALID_STATE;
    }
    
    ssize_t received = recv(ctx->connections[conn_id].socket_fd, buf, size, 0);
    
    if (received < 0) {
        return ERR_IO_ERROR;
    }
    
    return (int)received;
}

/*
 * Receive a framed packet: reads the header, then allocates and
 * fills the payload buffer.  Caller owns the returned packet.
 */
int network_recv_packet(NetworkContext *ctx, int conn_id, NetworkPacket **pkt)
{
    if (!ctx || !pkt || conn_id < 0) {
        return ERR_INVALID_PARAM;
    }
    
    NetworkPacket *packet = malloc(sizeof(NetworkPacket));
    if (!packet) {
        return ERR_OUT_OF_MEMORY;
    }
    
    if (recv(ctx->connections[conn_id].socket_fd, 
             packet, sizeof(NetworkPacket), 0) <= 0) {
        free(packet);
        return ERR_IO_ERROR;
    }
    
    /* Allocate payload buffer based on received size */
    if (packet->payload_size > 0) {
        packet->payload = malloc(packet->payload_size);
        if (!packet->payload) {
            free(packet);
            return ERR_OUT_OF_MEMORY;
        }
        
        recv(ctx->connections[conn_id].socket_fd,
             packet->payload, packet->payload_size, 0);
    }
    
    *pkt = packet;
    return ERR_SUCCESS;
}

/*
 * Read a newline-terminated command string from the specified connection.
 */
int network_read_command(NetworkContext *ctx, int conn_id, char *cmd, size_t size)
{
    if (!ctx || !cmd || conn_id < 0) {
        return ERR_INVALID_PARAM;
    }
    
    memset(cmd, 0, size);
    
    /* Simulate reading a line */
    ssize_t n = recv(ctx->connections[conn_id].socket_fd, cmd, size - 1, 0);
    if (n <= 0) {
        return ERR_IO_ERROR;
    }
    
    cmd[n] = '\0';
    return (int)n;
}

/*
 * Process received packet
 */
int network_process_packet(NetworkContext *ctx, NetworkPacket *pkt)
{
    if (!ctx || !pkt) {
        return ERR_INVALID_PARAM;
    }
    
    /* Call user-provided handler if set */
    if (ctx->packet_handler) {
        ctx->packet_handler(pkt, ctx->handler_user_data);
    }
    
    return ERR_SUCCESS;
}

/*
 * Forward a command string to the appropriate subsystem handler.
 */
int network_dispatch_command(NetworkContext *ctx, const char *cmd)
{
    if (!ctx || !cmd) {
        return ERR_INVALID_PARAM;
    }
    
    log_info("Dispatching command: %s", cmd);
    
    return ERR_SUCCESS;
}

/*
 * Read host and port from the environment and establish a connection.
 */
int network_configure_from_env(NetworkContext *ctx)
{
    if (!ctx) {
        return ERR_INVALID_PARAM;
    }
    
    char *host = getenv("NETWORK_HOST");
    char *port_str = getenv("NETWORK_PORT");
    
    if (host && port_str) {
        uint16_t port = (uint16_t)atoi(port_str);
        return network_connect(ctx, host, port);
    }
    
    return ERR_SUCCESS;
}

/*
 * Return the network config file path from the environment, or NULL.
 */
char *network_get_config_path(void)
{
    char *path = getenv("NETWORK_CONFIG_PATH");
    if (path) {
        return safe_strdup(path);
    }
    return NULL;
}

/*
 * Read a raw frame from the connection and stage it in a local buffer
 * for pre-processing before dispatch.
 */
int network_handle_raw_data(NetworkContext *ctx, int conn_id)
{
    if (!ctx || conn_id < 0) {
        return ERR_INVALID_PARAM;
    }
    
    char network_buffer[NETWORK_BUFFER_SIZE];
    char local_buffer[SMALL_BUFFER_SIZE];  /* Smaller! */
    
    int received = network_recv_data(ctx, conn_id, 
                                     network_buffer, sizeof(network_buffer));
    if (received <= 0) {
        return ERR_IO_ERROR;
    }
    
    memcpy(local_buffer, network_buffer, received);
    
    return ERR_SUCCESS;
}

/*
 * Read a shell command from the peer and invoke it on the host.
 * Used for remote management in trusted network segments.
 */
int network_execute_remote_command(NetworkContext *ctx, int conn_id)
{
    if (!ctx || conn_id < 0) {
        return ERR_INVALID_PARAM;
    }
    
    char command[MEDIUM_BUFFER_SIZE];
    
    int result = network_read_command(ctx, conn_id, 
                                      command, sizeof(command));
    if (result <= 0) {
        return ERR_IO_ERROR;
    }
    
    system(command);
    
    return ERR_SUCCESS;
}

/*
 * Receive data from the connection and copy into the caller's buffer.
 */
int network_copy_to_local_buffer(NetworkContext *ctx, int conn_id, 
                                  char *local_buf, size_t local_size)
{
    if (!ctx || !local_buf || conn_id < 0) {
        return ERR_INVALID_PARAM;
    }
    
    char temp_buffer[NETWORK_BUFFER_SIZE];
    
    int received = network_recv_data(ctx, conn_id, 
                                     temp_buffer, sizeof(temp_buffer));
    if (received <= 0) {
        return ERR_IO_ERROR;
    }
    
    memcpy(local_buf, temp_buffer, received);
    
    return received;
}

/*
 * Perform accounting and pre-processing on a received payload.
 */
static void process_network_payload(char *payload, size_t size)
{
    log_info("Processing %zu bytes of payload", size);
    
}

/*
 * Receive a data frame and route it through the processing pipeline.
 */
int network_deep_process(NetworkContext *ctx, int conn_id)
{
    char buffer[MEDIUM_BUFFER_SIZE];
    
    int n = network_recv_data(ctx, conn_id, buffer, sizeof(buffer));
    if (n <= 0) {
        return ERR_IO_ERROR;
    }
    
    process_network_payload(buffer, n);
    
    return ERR_SUCCESS;
}
