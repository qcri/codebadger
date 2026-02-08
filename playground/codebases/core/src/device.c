/*
 * device.c - Device emulation implementation
 * 
 * Contains state machine, deep call chains (5+ levels),
 * and cross-module data flows for comprehensive testing.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../include/device.h"

/*
 * Create device manager
 */
DeviceManager *device_manager_create(void)
{
    DeviceManager *dm = malloc(sizeof(DeviceManager));
    if (!dm) {
        return NULL;
    }
    
    dm->devices = NULL;
    dm->device_count = 0;
    dm->memory = NULL;
    dm->network = NULL;
    dm->config = NULL;
    
    return dm;
}

/*
 * Destroy device manager and all devices
 */
void device_manager_destroy(DeviceManager *dm)
{
    if (!dm) {
        return;
    }
    
    /* Destroy all devices */
    Device *dev = dm->devices;
    while (dev) {
        Device *next = dev->next;
        device_destroy(dev);
        dev = next;
    }
    
    /* Note: memory, network, config are external - don't free here */
    
    free(dm);
}

/*
 * STATE MACHINE: Convert state to string
 */
const char *device_state_to_string(DeviceState state)
{
    switch (state) {
        case DEVICE_STATE_UNINIT:     return "UNINIT";
        case DEVICE_STATE_INIT:       return "INIT";
        case DEVICE_STATE_CONFIGURED: return "CONFIGURED";
        case DEVICE_STATE_RUNNING:    return "RUNNING";
        case DEVICE_STATE_PAUSED:     return "PAUSED";
        case DEVICE_STATE_ERROR:      return "ERROR";
        case DEVICE_STATE_SHUTDOWN:   return "SHUTDOWN";
        default:                      return "UNKNOWN";
    }
}

/*
 * STATE MACHINE: Validate state transition
 */
static bool is_valid_transition(DeviceState current, DeviceState next)
{
    switch (current) {
        case DEVICE_STATE_UNINIT:
            return next == DEVICE_STATE_INIT || next == DEVICE_STATE_ERROR;
            
        case DEVICE_STATE_INIT:
            return next == DEVICE_STATE_CONFIGURED || 
                   next == DEVICE_STATE_ERROR ||
                   next == DEVICE_STATE_SHUTDOWN;
                   
        case DEVICE_STATE_CONFIGURED:
            return next == DEVICE_STATE_RUNNING || 
                   next == DEVICE_STATE_ERROR ||
                   next == DEVICE_STATE_SHUTDOWN;
                   
        case DEVICE_STATE_RUNNING:
            return next == DEVICE_STATE_PAUSED || 
                   next == DEVICE_STATE_ERROR ||
                   next == DEVICE_STATE_SHUTDOWN;
                   
        case DEVICE_STATE_PAUSED:
            return next == DEVICE_STATE_RUNNING || 
                   next == DEVICE_STATE_SHUTDOWN;
                   
        case DEVICE_STATE_ERROR:
            return next == DEVICE_STATE_SHUTDOWN ||
                   next == DEVICE_STATE_INIT;  /* Reset recovery */
                   
        case DEVICE_STATE_SHUTDOWN:
            return next == DEVICE_STATE_UNINIT;
            
        default:
            return false;
    }
}

/*
 * STATE MACHINE: Transition device state
 */
int device_transition_state(Device *dev, DeviceState new_state)
{
    if (!dev) {
        return ERR_INVALID_PARAM;
    }
    
    if (!is_valid_transition(dev->state, new_state)) {
        log_error("Invalid state transition: %s -> %s",
                  device_state_to_string(dev->state),
                  device_state_to_string(new_state));
        return ERR_INVALID_STATE;
    }
    
    log_info("Device %s: %s -> %s", dev->name,
             device_state_to_string(dev->state),
             device_state_to_string(new_state));
    
    dev->state = new_state;
    return ERR_SUCCESS;
}

/*
 * STATE MACHINE: Process event and transition
 */
int device_process_state_machine(Device *dev, int event)
{
    if (!dev) {
        return ERR_INVALID_PARAM;
    }
    
    DeviceState next_state = dev->state;
    
    switch (dev->state) {
        case DEVICE_STATE_UNINIT:
            if (event == 1) {  /* INIT event */
                next_state = DEVICE_STATE_INIT;
            }
            break;
            
        case DEVICE_STATE_INIT:
            if (event == 2) {  /* CONFIGURE event */
                next_state = DEVICE_STATE_CONFIGURED;
            } else if (event < 0) {  /* ERROR event */
                next_state = DEVICE_STATE_ERROR;
            }
            break;
            
        case DEVICE_STATE_CONFIGURED:
            if (event == 3) {  /* START event */
                next_state = DEVICE_STATE_RUNNING;
            }
            break;
            
        case DEVICE_STATE_RUNNING:
            if (event == 4) {  /* PAUSE event */
                next_state = DEVICE_STATE_PAUSED;
            } else if (event == 5) {  /* STOP event */
                next_state = DEVICE_STATE_SHUTDOWN;
            }
            break;
            
        case DEVICE_STATE_PAUSED:
            if (event == 3) {  /* RESUME (same as START) */
                next_state = DEVICE_STATE_RUNNING;
            }
            break;
            
        default:
            break;
    }
    
    if (next_state != dev->state) {
        return device_transition_state(dev, next_state);
    }
    
    return ERR_SUCCESS;
}

/*
 * Create a new device
 */
Device *device_create(const char *name, DeviceType type)
{
    Device *dev = malloc(sizeof(Device));
    if (!dev) {
        return NULL;
    }
    
    safe_strcpy(dev->name, sizeof(dev->name), name ? name : "unnamed");
    dev->type = type;
    dev->state = DEVICE_STATE_UNINIT;
    dev->device_id = 0;
    memset(&dev->callbacks, 0, sizeof(dev->callbacks));
    dev->opaque_data = NULL;
    dev->mmio_region = NULL;
    dev->next = NULL;
    
    return dev;
}

/*
 * Destroy a device
 */
void device_destroy(Device *dev)
{
    if (!dev) {
        return;
    }
    
    if (dev->mmio_region) {
        memory_region_free(dev->mmio_region);
    }
    
    if (dev->opaque_data) {
        free(dev->opaque_data);
    }
    
    free(dev);
}

/*
 * Add device to manager
 */
int device_add(DeviceManager *dm, Device *dev)
{
    if (!dm || !dev) {
        return ERR_INVALID_PARAM;
    }
    
    dev->next = dm->devices;
    dm->devices = dev;
    dm->device_count++;
    
    return ERR_SUCCESS;
}

/*
 * Find device by name
 */
Device *device_find(DeviceManager *dm, const char *name)
{
    if (!dm || !name) {
        return NULL;
    }
    
    Device *dev = dm->devices;
    while (dev) {
        if (strcmp(dev->name, name) == 0) {
            return dev;
        }
        dev = dev->next;
    }
    
    return NULL;
}

/*
 * Remove device from manager
 */
int device_remove(DeviceManager *dm, const char *name)
{
    if (!dm || !name) {
        return ERR_INVALID_PARAM;
    }
    
    Device **pp = &dm->devices;
    while (*pp) {
        if (strcmp((*pp)->name, name) == 0) {
            Device *to_remove = *pp;
            *pp = to_remove->next;
            device_destroy(to_remove);
            dm->device_count--;
            return ERR_SUCCESS;
        }
        pp = &(*pp)->next;
    }
    
    return ERR_NOT_FOUND;
}

/*
 * Register callbacks for device
 */
int device_register_callbacks(Device *dev, DeviceCallbacks *cbs)
{
    if (!dev || !cbs) {
        return ERR_INVALID_PARAM;
    }
    
    dev->callbacks = *cbs;
    return ERR_SUCCESS;
}

/*
 * Dispatch read operation through callback
 */
int device_dispatch_read(Device *dev, uint64_t addr, void *data, size_t size)
{
    if (!dev) {
        return ERR_INVALID_PARAM;
    }
    
    if (!dev->callbacks.read) {
        log_error("No read callback registered for device %s", dev->name);
        return ERR_INVALID_STATE;
    }
    
    return dev->callbacks.read(dev->opaque_data, addr, data, size);
}

/*
 * Dispatch write operation through callback
 */
int device_dispatch_write(Device *dev, uint64_t addr, const void *data, size_t size)
{
    if (!dev) {
        return ERR_INVALID_PARAM;
    }
    
    if (!dev->callbacks.write) {
        log_error("No write callback registered for device %s", dev->name);
        return ERR_INVALID_STATE;
    }
    
    return dev->callbacks.write(dev->opaque_data, addr, data, size);
}

/*
 * Dispatch IRQ through callback
 */
int device_dispatch_irq(Device *dev, int irq_num)
{
    if (!dev) {
        return ERR_INVALID_PARAM;
    }
    
    if (!dev->callbacks.irq_handler) {
        return ERR_SUCCESS;  /* No handler is OK */
    }
    
    return dev->callbacks.irq_handler(dev->opaque_data, irq_num);
}

/*
 * DEEP CALL CHAIN - Level 6 (innermost)
 */
static int device_internal_finalize(DeviceManager *dm)
{
    log_debug("Device internal finalize");
    
    /* Mark all devices as ready */
    Device *dev = dm->devices;
    while (dev) {
        if (dev->state == DEVICE_STATE_INIT) {
            device_transition_state(dev, DEVICE_STATE_CONFIGURED);
        }
        dev = dev->next;
    }
    
    return ERR_SUCCESS;
}

/*
 * DEEP CALL CHAIN - Level 5
 */
int device_finalize_init(DeviceManager *dm)
{
    if (!dm) {
        return ERR_INVALID_PARAM;
    }
    
    log_debug("Device finalize init (level 5)");
    return device_internal_finalize(dm);
}

/*
 * DEEP CALL CHAIN - Level 4
 */
int device_start(DeviceManager *dm)
{
    if (!dm) {
        return ERR_INVALID_PARAM;
    }
    
    log_debug("Device start (level 4)");
    return device_finalize_init(dm);
}

/*
 * DEEP CALL CHAIN - Level 3
 */
int device_register_handlers(DeviceManager *dm)
{
    if (!dm) {
        return ERR_INVALID_PARAM;
    }
    
    log_debug("Device register handlers (level 3)");
    
    /* Register default callbacks for each device */
    Device *dev = dm->devices;
    while (dev) {
        if (dev->state == DEVICE_STATE_INIT &&
            !dev->callbacks.read && !dev->callbacks.write) {
            /* No callbacks set - use defaults */
        }
        dev = dev->next;
    }
    
    return device_start(dm);
}

/*
 * DEEP CALL CHAIN - Level 2
 */
int device_setup_io(DeviceManager *dm, MemoryController *mc)
{
    if (!dm) {
        return ERR_INVALID_PARAM;
    }
    
    log_debug("Device setup IO (level 2)");
    
    dm->memory = mc;
    
    /* Setup MMIO regions for each device */
    if (mc) {
        Device *dev = dm->devices;
        uint64_t mmio_base = 0x10000000;
        
        while (dev) {
            dev->mmio_region = memory_region_create(
                dev->name, 4096,
                MEM_PERM_READ | MEM_PERM_WRITE,
                MEM_TYPE_MMIO
            );
            
            if (dev->mmio_region) {
                memory_region_add(mc, dev->mmio_region);
            }
            
            mmio_base += 0x1000;
            dev = dev->next;
        }
    }
    
    return device_register_handlers(dm);
}

/*
 * DEEP CALL CHAIN - Level 1
 */
int device_configure(DeviceManager *dm, ConfigContext *cfg)
{
    if (!dm) {
        return ERR_INVALID_PARAM;
    }
    
    log_debug("Device configure (level 1)");
    
    dm->config = cfg;
    
    /* Apply configuration to devices */
    if (cfg) {
        const char *debug_mode = config_get_string(cfg, "debug");
        if (debug_mode && strcmp(debug_mode, "true") == 0) {
            log_info("Debug mode enabled");
        }
    }
    
    return device_setup_io(dm, dm->memory);
}

/*
 * DEEP CALL CHAIN - Level 0 (entry point)
 * Call chain: device_init -> device_configure -> device_setup_io 
 *             -> device_register_handlers -> device_start 
 *             -> device_finalize_init -> device_internal_finalize
 */
int device_init(DeviceManager *dm)
{
    if (!dm) {
        return ERR_INVALID_PARAM;
    }
    
    log_info("Device init (level 0) - starting deep call chain");
    
    /* Initialize each device to INIT state */
    Device *dev = dm->devices;
    while (dev) {
        device_transition_state(dev, DEVICE_STATE_INIT);
        dev = dev->next;
    }
    
    /* Continue through deep call chain */
    return device_configure(dm, dm->config);
}

/*
 * DMA read through device
 */
int device_dma_read(Device *dev, MemoryController *mc, 
                    uint64_t addr, void *buf, size_t size)
{
    if (!dev || !mc || !buf) {
        return ERR_INVALID_PARAM;
    }
    
    return memory_read(mc, addr, buf, size);
}

/*
 * DMA write through device
 */
int device_dma_write(Device *dev, MemoryController *mc,
                     uint64_t addr, const void *buf, size_t size)
{
    if (!dev || !mc || !buf) {
        return ERR_INVALID_PARAM;
    }
    
    return memory_write(mc, addr, buf, size);
}

/*
 * VULNERABLE: Process untrusted data from device
 * Cross-module taint flow to memory operations
 */
int device_process_untrusted_data(Device *dev, void *data, size_t size)
{
    if (!dev || !data) {
        return ERR_INVALID_PARAM;
    }
    
    /* Allocate local buffer */
    char local_buffer[SMALL_BUFFER_SIZE];
    
    /* VULNERABLE: No size check before copy */
    memcpy(local_buffer, data, size);  /* BUFFER OVERFLOW */
    
    log_debug("Device %s processed %zu bytes", dev->name, size);
    
    return ERR_SUCCESS;
}

/*
 * VULNERABLE: Handle command from network
 * Cross-module taint flow: network -> device -> command execution
 */
int device_handle_network_command(Device *dev, NetworkContext *net, int conn_id)
{
    if (!dev || !net) {
        return ERR_INVALID_PARAM;
    }
    
    char command_buffer[MEDIUM_BUFFER_SIZE];
    
    /* TAINT SOURCE: Read from network */
    int n = network_read_command(net, conn_id, 
                                 command_buffer, sizeof(command_buffer));
    if (n <= 0) {
        return ERR_IO_ERROR;
    }
    
    /* Process command based on device type */
    if (strncmp(command_buffer, "exec:", 5) == 0) {
        /* VULNERABLE SINK: Execute command */
        system(command_buffer + 5);  /* COMMAND INJECTION */
    } else if (strncmp(command_buffer, "debug:", 6) == 0) {
        /* VULNERABLE: Format string */
        printf(command_buffer + 6);  /* FORMAT STRING */
    }
    
    return ERR_SUCCESS;
}

/*
 * Cross-file vulnerability chain
 * This function ties together vulnerabilities across multiple modules
 */
int device_full_vulnerability_chain(DeviceManager *dm, int conn_id)
{
    if (!dm || !dm->network) {
        return ERR_INVALID_PARAM;
    }
    
    char buffer[NETWORK_BUFFER_SIZE];
    
    /* Step 1: TAINT SOURCE from network */
    int n = network_recv_data(dm->network, conn_id, 
                              buffer, sizeof(buffer));
    if (n <= 0) {
        return ERR_IO_ERROR;
    }
    
    /* Step 2: Pass tainted data to memory module */
    if (dm->memory) {
        /* VULNERABLE: No size validation */
        memory_process_untrusted(dm->memory, buffer, n);
    }
    
    /* Step 3: Also use as command (if it starts with "cmd:") */
    if (strncmp(buffer, "cmd:", 4) == 0) {
        /* VULNERABLE SINK: Command injection */
        system(buffer + 4);
    }
    
    return ERR_SUCCESS;
}
