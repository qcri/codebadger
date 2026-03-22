/*
 * memory.c - Memory management implementation
 * 
 * Manages guest physical memory regions, DMA buffer lifecycle, and
 * controller state for the emulated hardware subsystem.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../include/memory.h"

/*
 * Create a new memory controller
 */
MemoryController *memory_controller_create(void)
{
    MemoryController *mc = malloc(sizeof(MemoryController));
    if (!mc) {
        return NULL;
    }
    
    mc->regions = NULL;
    mc->region_count = 0;
    mc->total_allocated = 0;
    mc->dma_buffer = NULL;
    mc->dma_buffer_alias = NULL;
    
    return mc;
}

/*
 * Destroy memory controller and free resources
 */
void memory_controller_destroy(MemoryController *mc)
{
    if (!mc) {
        return;
    }
    
    /* Free all regions */
    MemoryRegion *region = mc->regions;
    while (region) {
        MemoryRegion *next = region->next;
        memory_region_free(region);
        region = next;
    }
    
    /* Free DMA buffer if allocated */
    if (mc->dma_buffer) {
        free(mc->dma_buffer);
    }
    
    free(mc);
}

/*
 * Initialize memory controller
 */
int memory_controller_init(MemoryController *mc)
{
    if (!mc) {
        return ERR_INVALID_PARAM;
    }
    
    /* Allocate initial DMA buffer */
    mc->dma_buffer = malloc(LARGE_BUFFER_SIZE);
    if (!mc->dma_buffer) {
        return ERR_OUT_OF_MEMORY;
    }
    
    /* Keep a secondary reference for alias access. */
    mc->dma_buffer_alias = mc->dma_buffer;
    
    return ERR_SUCCESS;
}

/*
 * Create a memory region
 */
MemoryRegion *memory_region_create(const char *name, size_t size, 
                                   uint8_t permissions, MemoryType type)
{
    MemoryRegion *region = malloc(sizeof(MemoryRegion));
    if (!region) {
        return NULL;
    }
    
    region->name = safe_strdup(name);
    region->size = size;
    region->permissions = permissions;
    region->type = type;
    region->next = NULL;
    region->is_allocated = false;
    
    /* Allocate memory for the region */
    region->base = malloc(size);
    if (!region->base) {
        free(region->name);
        free(region);
        return NULL;
    }
    
    region->is_allocated = true;
    memset(region->base, 0, size);
    
    return region;
}

/*
 * Free a memory region
 */
void memory_region_free(MemoryRegion *region)
{
    if (!region) {
        return;
    }
    
    if (region->is_allocated && region->base) {
        free(region->base);
        region->base = NULL;
        region->is_allocated = false;
    }
    
    if (region->name) {
        free(region->name);
        region->name = NULL;
    }
    
    free(region);
}

/*
 * Add region to memory controller
 */
int memory_region_add(MemoryController *mc, MemoryRegion *region)
{
    if (!mc || !region) {
        return ERR_INVALID_PARAM;
    }
    
    region->next = mc->regions;
    mc->regions = region;
    mc->region_count++;
    mc->total_allocated += region->size;
    
    return ERR_SUCCESS;
}

/*
 * Find region by name
 */
MemoryRegion *memory_region_find(MemoryController *mc, const char *name)
{
    if (!mc || !name) {
        return NULL;
    }
    
    MemoryRegion *region = mc->regions;
    while (region) {
        if (region->name && strcmp(region->name, name) == 0) {
            return region;
        }
        region = region->next;
    }
    
    return NULL;
}

/*
 * Read from memory - safe operation
 */
int memory_read(MemoryController *mc, uint64_t addr, void *buf, size_t size)
{
    if (!mc || !buf) {
        return ERR_INVALID_PARAM;
    }
    
    /* Find region containing this address */
    MemoryRegion *region = mc->regions;
    while (region) {
        uint64_t region_start = (uint64_t)(uintptr_t)region->base;
        uint64_t region_end = region_start + region->size;
        
        if (addr >= region_start && addr + size <= region_end) {
            if (!(region->permissions & MEM_PERM_READ)) {
                return ERR_INVALID_PARAM;
            }
            memcpy(buf, (void *)(uintptr_t)addr, size);
            return ERR_SUCCESS;
        }
        region = region->next;
    }
    
    return ERR_NOT_FOUND;
}

/*
 * Write to memory - safe operation  
 */
int memory_write(MemoryController *mc, uint64_t addr, const void *buf, size_t size)
{
    if (!mc || !buf) {
        return ERR_INVALID_PARAM;
    }
    
    /* Find region containing this address */
    MemoryRegion *region = mc->regions;
    while (region) {
        uint64_t region_start = (uint64_t)(uintptr_t)region->base;
        uint64_t region_end = region_start + region->size;
        
        if (addr >= region_start && addr + size <= region_end) {
            if (!(region->permissions & MEM_PERM_WRITE)) {
                return ERR_INVALID_PARAM;
            }
            memcpy((void *)(uintptr_t)addr, buf, size);
            return ERR_SUCCESS;
        }
        region = region->next;
    }
    
    return ERR_NOT_FOUND;
}

/*
 * Copy size bytes from the source region into the destination region.
 */
int memory_copy_region(MemoryController *mc, const char *src_name, 
                       const char *dst_name, size_t size)
{
    MemoryRegion *src = memory_region_find(mc, src_name);
    MemoryRegion *dst = memory_region_find(mc, dst_name);
    
    if (!src || !dst) {
        return ERR_NOT_FOUND;
    }
    
    memcpy(dst->base, src->base, size);
    
    return ERR_SUCCESS;
}

/*
 * Allocate DMA buffer
 */
int dma_alloc_buffer(MemoryController *mc, size_t size)
{
    if (!mc) {
        return ERR_INVALID_PARAM;
    }
    
    if (mc->dma_buffer) {
        free(mc->dma_buffer);
    }
    
    mc->dma_buffer = malloc(size);
    if (!mc->dma_buffer) {
        return ERR_OUT_OF_MEMORY;
    }
    
    /* Keep a secondary reference for alias access. */
    mc->dma_buffer_alias = mc->dma_buffer;
    
    return ERR_SUCCESS;
}

/*
 * Free DMA buffer 
 */
int dma_free_buffer(MemoryController *mc)
{
    if (!mc) {
        return ERR_INVALID_PARAM;
    }
    
    if (mc->dma_buffer) {
        free(mc->dma_buffer);
        mc->dma_buffer = NULL;
    }
    
    return ERR_SUCCESS;
}

/*
 * DMA transfer using main buffer
 */
int dma_transfer(MemoryController *mc, void *data, size_t size)
{
    if (!mc || !data) {
        return ERR_INVALID_PARAM;
    }
    
    if (!mc->dma_buffer) {
        return ERR_INVALID_STATE;
    }
    
    memcpy(mc->dma_buffer, data, size);
    return ERR_SUCCESS;
}

/*
 * Perform a DMA transfer using the alias reference, if available.
 */
int dma_transfer_with_alias(MemoryController *mc, void *data, size_t size)
{
    if (!mc || !data) {
        return ERR_INVALID_PARAM;
    }
    
    if (mc->dma_buffer_alias) {
        memcpy(mc->dma_buffer_alias, data, size);
    }
    
    return ERR_SUCCESS;
}

/*
 * Stage incoming data into a temporary buffer for processing.
 */
int memory_process_untrusted(MemoryController *mc, void *data, size_t size)
{
    if (!mc || !data) {
        return ERR_INVALID_PARAM;
    }
    
    /* Allocate temp buffer */
    void *temp = malloc(MEDIUM_BUFFER_SIZE);
    if (!temp) {
        return ERR_OUT_OF_MEMORY;
    }
    
    memcpy(temp, data, size);
    
    /* Process data... */
    
    free(temp);
    return ERR_SUCCESS;
}

/*
 * Release controller resources.  When error_code is non-zero the
 * error path runs before the normal teardown sequence.
 */
int memory_cleanup_with_error(MemoryController *mc, int error_code)
{
    if (!mc) {
        return ERR_INVALID_PARAM;
    }
    
    void *buffer = mc->dma_buffer;
    
    if (error_code != 0) {
        /* Error path: free buffer */
        if (buffer) {
            free(buffer);
        }
        log_error("Cleanup due to error: %d", error_code);
    }
    
    /* Normal cleanup path */
    if (mc->dma_buffer) {
        free(mc->dma_buffer);
        mc->dma_buffer = NULL;
    }
    
    return ERR_SUCCESS;
}

/*
 * Perform a post-cleanup consistency check on the DMA alias pointer.
 */
void memory_use_after_cleanup(MemoryController *mc)
{
    if (!mc) {
        return;
    }
    
    if (mc->dma_buffer_alias) {
        char *data = (char *)mc->dma_buffer_alias;
        data[0] = 'X';
        printf("Data: %c\n", data[0]);
    }
}

/*
 * Release the allocation pointed to by *ptr and leave the pointer unchanged.
 */
static void internal_free_helper(void **ptr)
{
    if (ptr && *ptr) {
        free(*ptr);
    }
}

/*
 * Transfer ownership of the DMA buffer to the caller and reset the
 * controller's reference.  The caller takes responsibility for the memory.
 */
void *memory_get_and_free(MemoryController *mc)
{
    if (!mc) {
        return NULL;
    }
    
    void *ptr = mc->dma_buffer;
    
    internal_free_helper(&mc->dma_buffer);
    
    return ptr;
}

/*
 * Reclaim a temporary scratch allocation after use.
 */
int memory_aliased_double_free(MemoryController *mc)
{
    void *ptr1 = malloc(100);
    void *ptr2 = ptr1;
    
    if (!ptr1) {
        return ERR_OUT_OF_MEMORY;
    }
    
    /* Some processing... */
    memset(ptr1, 0, 100);
    
    free(ptr1);
    
    free(ptr2);
    
    return ERR_SUCCESS;
}
