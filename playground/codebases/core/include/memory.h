/*
 * memory.h - Memory management for QEMU-like emulator
 * 
 * Provides memory region management, allocation tracking,
 * and memory controller API for device emulation.
 * Contains patterns that test UAF and double-free detection.
 */

#ifndef MEMORY_H
#define MEMORY_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include "utils.h"

/* Memory region permissions */
#define MEM_PERM_READ   0x01
#define MEM_PERM_WRITE  0x02
#define MEM_PERM_EXEC   0x04

/* Memory region types */
typedef enum {
    MEM_TYPE_RAM,
    MEM_TYPE_ROM,
    MEM_TYPE_MMIO,
    MEM_TYPE_DMA
} MemoryType;

/* Memory region structure */
typedef struct MemoryRegion {
    char *name;
    void *base;
    size_t size;
    uint8_t permissions;
    MemoryType type;
    struct MemoryRegion *next;
    bool is_allocated;
} MemoryRegion;

/* Memory controller state */
typedef struct MemoryController {
    MemoryRegion *regions;
    size_t region_count;
    size_t total_allocated;
    void *dma_buffer;
    void *dma_buffer_alias;  /* For testing pointer aliasing */
} MemoryController;

/* Allocation tracking entry */
typedef struct AllocationEntry {
    void *ptr;
    size_t size;
    const char *file;
    int line;
    bool freed;
    struct AllocationEntry *next;
} AllocationEntry;

/* Memory controller API */
MemoryController *memory_controller_create(void);
void memory_controller_destroy(MemoryController *mc);
int memory_controller_init(MemoryController *mc);

/* Memory region management */
MemoryRegion *memory_region_create(const char *name, size_t size, 
                                   uint8_t permissions, MemoryType type);
void memory_region_free(MemoryRegion *region);
int memory_region_add(MemoryController *mc, MemoryRegion *region);
MemoryRegion *memory_region_find(MemoryController *mc, const char *name);

/* Memory operations - some deliberately vulnerable */
int memory_read(MemoryController *mc, uint64_t addr, void *buf, size_t size);
int memory_write(MemoryController *mc, uint64_t addr, const void *buf, size_t size);
int memory_copy_region(MemoryController *mc, const char *src_name, 
                       const char *dst_name, size_t size);

/* DMA operations - contain UAF patterns */
int dma_alloc_buffer(MemoryController *mc, size_t size);
int dma_free_buffer(MemoryController *mc);
int dma_transfer(MemoryController *mc, void *data, size_t size);
int dma_transfer_with_alias(MemoryController *mc, void *data, size_t size);

/* Functions containing deliberate vulnerabilities */
int memory_process_untrusted(MemoryController *mc, void *data, size_t size);
int memory_cleanup_with_error(MemoryController *mc, int error_code);
void memory_use_after_cleanup(MemoryController *mc);

/* Helper that frees memory but caller uses it after */
void *memory_get_and_free(MemoryController *mc);

#endif /* MEMORY_H */
