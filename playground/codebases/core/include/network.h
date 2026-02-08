/*
 * network.h - Network I/O for QEMU-like emulator
 * 
 * Provides network packet handling, socket operations,
 * and data reception. Contains taint source patterns.
 */

#ifndef NETWORK_H
#define NETWORK_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include "utils.h"

/* Network constants */
#define MAX_PACKET_SIZE     65536
#define DEFAULT_MTU         1500
#define MAX_CONNECTIONS     256
#define NETWORK_BUFFER_SIZE 4096

/* Packet types */
typedef enum {
    PACKET_TYPE_DATA,
    PACKET_TYPE_CONTROL,
    PACKET_TYPE_CONFIG,
    PACKET_TYPE_COMMAND
} PacketType;

/* Network packet structure */
typedef struct NetworkPacket {
    PacketType type;
    uint32_t seq_num;
    uint32_t payload_size;
    uint8_t *payload;
    struct NetworkPacket *next;
} NetworkPacket;

/* Connection state */
typedef struct NetworkConnection {
    int socket_fd;
    char remote_addr[64];
    uint16_t remote_port;
    bool is_connected;
    uint8_t recv_buffer[NETWORK_BUFFER_SIZE];
    size_t recv_buffer_len;
} NetworkConnection;

/* Network handler context */
typedef struct NetworkContext {
    NetworkConnection *connections;
    size_t connection_count;
    NetworkPacket *packet_queue;
    void (*packet_handler)(NetworkPacket *pkt, void *user_data);
    void *handler_user_data;
} NetworkContext;

/* Network API */
NetworkContext *network_create(void);
void network_destroy(NetworkContext *ctx);
int network_init(NetworkContext *ctx);

/* Connection management */
int network_connect(NetworkContext *ctx, const char *host, uint16_t port);
int network_listen(NetworkContext *ctx, uint16_t port);
int network_accept(NetworkContext *ctx);
void network_close_connection(NetworkContext *ctx, int conn_id);

/* Data reception - TAINT SOURCES */
int network_recv_data(NetworkContext *ctx, int conn_id, void *buf, size_t size);
int network_recv_packet(NetworkContext *ctx, int conn_id, NetworkPacket **pkt);
int network_read_command(NetworkContext *ctx, int conn_id, char *cmd, size_t size);

/* Packet processing */
int network_process_packet(NetworkContext *ctx, NetworkPacket *pkt);
int network_dispatch_command(NetworkContext *ctx, const char *cmd);

/* Environment-based configuration - TAINT SOURCE */
int network_configure_from_env(NetworkContext *ctx);
char *network_get_config_path(void);

/* Vulnerable patterns - tainted data flows */
int network_handle_raw_data(NetworkContext *ctx, int conn_id);
int network_execute_remote_command(NetworkContext *ctx, int conn_id);
int network_copy_to_local_buffer(NetworkContext *ctx, int conn_id, 
                                  char *local_buf, size_t local_size);

#endif /* NETWORK_H */
