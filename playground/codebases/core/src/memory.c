/*
 * memory.c - Memory management implementation
 * 
 * Contains deliberate UAF, double-free, and pointer aliasing vulnerabilities
 * for testing find_use_after_free and find_double_free tools.
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
    
    /* Create alias for UAF testing */
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
 * Copy between regions - may be vulnerable to size issues
 */
int memory_copy_region(MemoryController *mc, const char *src_name, 
                       const char *dst_name, size_t size)
{
    MemoryRegion *src = memory_region_find(mc, src_name);
    MemoryRegion *dst = memory_region_find(mc, dst_name);
    
    if (!src || !dst) {
        return ERR_NOT_FOUND;
    }
    
    /* VULNERABLE: No check that size <= dst->size */
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
    
    /* Create alias - sets up UAF pattern */
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
        /* NOTE: dma_buffer_alias still points to freed memory! */
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
 * DMA transfer using ALIAS - USE-AFTER-FREE vulnerability
 * After dma_free_buffer(), this uses freed memory via alias
 */
int dma_transfer_with_alias(MemoryController *mc, void *data, size_t size)
{
    if (!mc || !data) {
        return ERR_INVALID_PARAM;
    }
    
    /* UAF: dma_buffer_alias may point to freed memory */
    if (mc->dma_buffer_alias) {
        memcpy(mc->dma_buffer_alias, data, size);  /* UAF HERE */
    }
    
    return ERR_SUCCESS;
}

/*
 * Process untrusted data - contains memory vulnerability chain
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
    
    /* VULNERABLE: No check that size <= MEDIUM_BUFFER_SIZE */
    memcpy(temp, data, size);
    
    /* Process data... */
    
    free(temp);
    return ERR_SUCCESS;
}

/*
 * Cleanup with error path - DOUBLE-FREE vulnerability
 * Both error path and normal path free the same buffer
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
            free(buffer);  /* First free */
        }
        log_error("Cleanup due to error: %d", error_code);
        /* Fall through to normal cleanup... BUG! */
    }
    
    /* Normal cleanup path */
    if (mc->dma_buffer) {
        free(mc->dma_buffer);  /* DOUBLE-FREE when error_code != 0 */
        mc->dma_buffer = NULL;
    }
    
    return ERR_SUCCESS;
}

/*
 * USE-AFTER-FREE: Called after cleanup frees memory
 * Tests interprocedural UAF detection
 */
void memory_use_after_cleanup(MemoryController *mc)
{
    if (!mc) {
        return;
    }
    
    /* This may use freed memory if called after cleanup */
    if (mc->dma_buffer_alias) {
        /* UAF: dma_buffer_alias may have been freed */
        char *data = (char *)mc->dma_buffer_alias;
        data[0] = 'X';  /* UAF write */
        printf("Data: %c\n", data[0]);  /* UAF read */
    }
}

/*
 * Helper that frees memory - caller then uses it
 * Tests deep interprocedural UAF
 */
static void internal_free_helper(void **ptr)
{
    if (ptr && *ptr) {
        free(*ptr);
        /* Does NOT set *ptr = NULL */
    }
}

/*
 * Returns pointer after freeing it - UAF pattern
 */
void *memory_get_and_free(MemoryController *mc)
{
    if (!mc) {
        return NULL;
    }
    
    void *ptr = mc->dma_buffer;
    
    /* Free via helper - ptr still valid after */
    internal_free_helper(&mc->dma_buffer);
    
    /* Return the freed pointer - caller may use it */
    return ptr;  /* UAF: returning freed pointer */
}

/*
 * Demonstrating aliased pointer double-free
 */
int memory_aliased_double_free(MemoryController *mc)
{
    void *ptr1 = malloc(100);
    void *ptr2 = ptr1;  /* Alias */
    
    if (!ptr1) {
        return ERR_OUT_OF_MEMORY;
    }
    
    /* Some processing... */
    memset(ptr1, 0, 100);
    
    free(ptr1);  /* First free via ptr1 */
    
    /* Later... forgot ptr2 is same as ptr1 */
    free(ptr2);  /* DOUBLE-FREE via alias */
    
    return ERR_SUCCESS;
}
